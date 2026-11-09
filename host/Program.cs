using System.Net;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        if (args.Length == 0 || args is ["--help"] or ["help"])
        {
            Console.WriteLine("""
                Cantabile host 0.1.0
                stdio --workspace DIRECTORY [--permission edit|readOnly]
                serve --workspace DIRECTORY --hub-endpoint wss://HOST/desktop/UPSTREAM --credential-file PRIVATE_FILE [--permission edit|readOnly] [--health-port 8774]
                catalog --workspace DIRECTORY
                Environment: CANTABILE_CORE_LIBRARY (native library), CANTABILE_RENDERER (renderer executable), CANTABILE_SOUNDFONT (configured SoundFont).
                Changes stay in memory until project_save; saved projects and completed exports survive compatible restarts.
                """); return 0;
        }
        // Console-based fallback logging must never corrupt the raw MCP stdout stream.
        Console.SetOut(Console.Error);
        using var lifetime = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; lifetime.Cancel(); };
        using var terminate = OperatingSystem.IsLinux() ? PosixSignalRegistration.Create(PosixSignal.SIGTERM, x => { x.Cancel = true; lifetime.Cancel(); }) : null;
        try
        {
            if (args[0] is not ("stdio" or "serve" or "catalog")) throw new ArgumentException("Unknown command.");
            var options = Parse(args[1..]);
            if (!options.TryGetValue("workspace", out var directory)) throw new ArgumentException("--workspace is required.");
            var permission = options.GetValueOrDefault("permission", "readOnly") switch { "edit" => McpPermission.Edit, "readOnly" => McpPermission.ReadOnly, _ => throw new ArgumentException("Invalid permission.") };
            await using var host = new EditorHost(directory);
            if (args[0] == "catalog") { using var output = new StreamWriter(Console.OpenStandardOutput()); output.WriteLine(Catalog(host).ToJsonString()); return 0; }
            using var grant = await host.Boundary.EnableAsync(permission, lifetime.Token);
            if (args[0] == "stdio")
            {
                await SdkStdio.RunAsync(host, grant, lifetime.Token);
                return 0;
            }
            if (!options.TryGetValue("hub-endpoint", out var endpointText) || !options.TryGetValue("credential-file", out var credentialFile)) throw new ArgumentException("Hub endpoint and private credential file are required.");
            var settings = new HubConnectionSettings(new Uri(endpointText)); settings.Validate();
            credentialFile = Path.GetFullPath(credentialFile);
            string Credential()
            {
                Workspace.DemandRegular(credentialFile);
                if (OperatingSystem.IsLinux() && (File.GetUnixFileMode(credentialFile) & (UnixFileMode.GroupRead | UnixFileMode.GroupWrite | UnixFileMode.GroupExecute | UnixFileMode.OtherRead | UnixFileMode.OtherWrite | UnixFileMode.OtherExecute)) != 0) throw new McpFault("credential_permissions");
                string value = new System.Text.UTF8Encoding(false, true).GetString(Workspace.ReadBounded(credentialFile, 4096)).Trim();
                if (string.IsNullOrWhiteSpace(value) || value.Any(char.IsControl)) throw new McpFault(McpErrors.Unauthorized);
                return value;
            }
            _ = Credential();
            var provider = new HubConnectionProvider(settings, host.Boundary, grant, _ => ValueTask.FromResult(Credential()));
            int port = int.Parse(options.GetValueOrDefault("health-port", "8774"), System.Globalization.CultureInfo.InvariantCulture);
            if (port is < 1024 or > 65535) throw new ArgumentException("Invalid health port.");
            Task health = HealthAsync(host, port, lifetime.Token);
            try
            {
                await provider.StartAsync(lifetime.Token);
                while (true)
                {
                    await Task.Delay(TimeSpan.FromSeconds(60), lifetime.Token);
                    if (host.Boundary.Status.Current.LastError is not null) await provider.RefreshAsync(lifetime.Token);
                    if (health.IsFaulted) await health;
                }
            }
            finally { grant.Revoke(); lifetime.Cancel(); await provider.StopAsync(CancellationToken.None); try { await health; } catch (OperationCanceledException) { } }
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { return 0; }
        catch (Exception e)
        {
            // Do not print arguments, paths, payloads, or credential-bearing exception messages.
            string code = e is McpFault fault ? fault.Code : e is ArgumentException or FormatException ? "invalid_configuration" : "host_start_failed";
            Console.Error.WriteLine(JsonSerializer.Serialize(new { ok = false, error = new { code } })); return 1;
        }
    }
    public static JsonArray Catalog(EditorHost host)
    {
        var items = JsonNode.Parse(host.Boundary.DescribeCatalog().GetRawText())!.AsArray();
        foreach (var item in items)
        {
            string name = item!["name"]!.GetValue<string>();
            bool readOnly = name == "mcp.context" || host.Boundary.Tools[name].Kind == OperationKind.Query;
            bool destructive = name is "edit_batch" or "history_undo" or "history_redo" or "project_load" or "project_open" or "midi_import" or "file_release" or "audio_cancel";
            item["annotations"] = JsonSerializer.SerializeToNode(new { readOnlyHint = readOnly, destructiveHint = destructive, openWorldHint = false });
        }
        return items;
    }
    private static Dictionary<string, string> Parse(string[] args)
    {
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        string[] allowed = ["workspace", "permission", "hub-endpoint", "credential-file", "health-port"];
        for (int i = 0; i < args.Length; i += 2)
        {
            if (!args[i].StartsWith("--", StringComparison.Ordinal) || !allowed.Contains(args[i][2..]) || i + 1 >= args.Length || !values.TryAdd(args[i][2..], args[i + 1])) throw new ArgumentException("Invalid option.");
        }
        return values;
    }
    private static async Task HealthAsync(EditorHost host, int port, CancellationToken token)
    {
        using var listener = new HttpListener(); listener.Prefixes.Add($"http://127.0.0.1:{port}/"); listener.Start();
        using var stop = token.Register(listener.Close);
        try
        {
            while (!token.IsCancellationRequested)
            {
                var request = await listener.GetContextAsync().WaitAsync(token);
                try
                {
                    if (request.Request.HttpMethod != "GET" || request.Request.Url?.AbsolutePath != "/healthz") { request.Response.StatusCode = 404; continue; }
                    var body = await host.InvokeAsync(() => JsonSerializer.SerializeToUtf8Bytes(new { ready = true, product = "flamoris.cantabile", version = "0.1.0",
                        revision = host.Snapshot.Revision, dirty = host.Dirty, hub_connected = host.Boundary.Status.Current.Connected, audio_configured = host.Renders.Available }), token);
                    request.Response.ContentType = "application/json"; request.Response.ContentLength64 = body.Length; await request.Response.OutputStream.WriteAsync(body, token);
                }
                finally { request.Response.Close(); }
            }
        }
        catch (Exception e) when (token.IsCancellationRequested && e is HttpListenerException or ObjectDisposedException or OperationCanceledException) { }
    }
}
