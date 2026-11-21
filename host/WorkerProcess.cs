using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

// One bounded process per plugin operation. Raw stdout/stderr never reaches MCP.
internal static class WorkerProcess
{
    public static async Task<JsonElement> Run(string executable, IEnumerable<string> arguments, string directory,
        TimeSpan timeout, CancellationToken token, int stdoutMaximum = 4 * 1024 * 1024)
    {
        using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(token);
        lifetime.CancelAfter(timeout);
        using var process = new Process { StartInfo = new ProcessStartInfo(Path.GetFullPath(executable)) {
            WorkingDirectory = directory, RedirectStandardOutput = true, RedirectStandardError = true,
            UseShellExecute = false, CreateNoWindow = true } };
        foreach (var argument in arguments) process.StartInfo.ArgumentList.Add(argument);
        SafeEnvironment(process.StartInfo);
        if (!process.Start()) throw new McpFault("vst_start_failed");
        Task<string> output = Read(process.StandardOutput, stdoutMaximum, lifetime.Token);
        Task<string> errors = Read(process.StandardError, 16384, lifetime.Token);
        try
        {
            var pending = new List<Task> { process.WaitForExitAsync(lifetime.Token), output, errors };
            while (pending.Count > 0) { var done = await Task.WhenAny(pending); await done; pending.Remove(done); }
        }
        catch
        {
            try { process.Kill(entireProcessTree: true); await process.WaitForExitAsync(CancellationToken.None); } catch (InvalidOperationException) { }
            try { await Task.WhenAll(output, errors); } catch { }
            if (lifetime.IsCancellationRequested && !token.IsCancellationRequested) throw new McpFault("vst_timeout");
            throw;
        }
        JsonElement result;
        try { using var json = JsonDocument.Parse(await output, new() { MaxDepth = 32 }); result = json.RootElement.Clone(); }
        catch (JsonException) { throw new McpFault("vst_worker_crashed"); }
        if (result.ValueKind != JsonValueKind.Object || !result.TryGetProperty("ok", out var ok) || ok.ValueKind != JsonValueKind.True || process.ExitCode != 0)
            throw WorkerFailure.From(result);
        token.ThrowIfCancellationRequested();
        return result;
    }
    public static void SafeEnvironment(ProcessStartInfo info)
    {
        string[] retained = ["PATH", "LD_LIBRARY_PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL"];
        if (OperatingSystem.IsWindows()) retained = [..retained, "APPDATA", "LOCALAPPDATA", "USERPROFILE", "PROGRAMDATA", "PUBLIC", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432", "SYSTEMDRIVE"];
        var safe = info.Environment.Where(p => retained.Contains(p.Key, StringComparer.OrdinalIgnoreCase)).ToArray();
        info.Environment.Clear(); foreach (var entry in safe) info.Environment[entry.Key] = entry.Value;
    }
    private static async Task<string> Read(StreamReader stream, int maximum, CancellationToken token)
    {
        var text = new StringBuilder(); var buffer = new char[4096]; int count;
        while ((count = await stream.ReadAsync(buffer.AsMemory(), token)) > 0)
        { if (text.Length + count > maximum) throw new McpFault("vst_output_limit"); text.Append(buffer, 0, count); }
        return text.ToString();
    }
}

internal sealed class WorkerFailure(string code, JsonElement summary) : Exception
{
    public string Code { get; } = code;
    public JsonElement Summary { get; } = summary;
    public static string SafeCode(string? code) => code is { Length: > 0 and <= 80 } && code.All(c => char.IsAsciiLetterOrDigit(c) || c == '_') ? code : "vst_worker_failed";
    public static WorkerFailure From(JsonElement result)
    {
        string code = "vst_worker_failed"; bool? attempted = null, restored = null;
        var audit = new List<object>();
        if (result.ValueKind == JsonValueKind.Object && result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.Object)
        {
            if (error.TryGetProperty("code", out var c) && c.ValueKind == JsonValueKind.String) code = SafeCode(c.GetString());
            if (error.TryGetProperty("rollback", out var rollback) && rollback.ValueKind == JsonValueKind.Object)
            {
                if (rollback.TryGetProperty("attempted", out var a) && a.ValueKind is JsonValueKind.True or JsonValueKind.False) attempted = a.GetBoolean();
                if (rollback.TryGetProperty("restored", out var r) && r.ValueKind is JsonValueKind.True or JsonValueKind.False) restored = r.GetBoolean();
            }
            if (error.TryGetProperty("audit", out var events) && events.ValueKind == JsonValueKind.Array)
                foreach (var entry in events.EnumerateArray().Take(256))
                {
                    if (entry.ValueKind != JsonValueKind.Object || !entry.TryGetProperty("index", out var i) || !i.TryGetInt32(out int index) || index is < 0 or > 255 ||
                        !entry.TryGetProperty("op", out var o) || o.ValueKind != JsonValueKind.String || !entry.TryGetProperty("status", out var s)) continue;
                    string? op = o.GetString(), status = s.ValueKind == JsonValueKind.String ? s.GetString() : null;
                    if (op is "inspect" or "list_parameters" or "get_parameter" or "set_parameter" or "midi" or "render" or "save_state" or "load_state" && status is "completed" or "failed")
                        audit.Add(new { index, op, status });
                }
        }
        return new(code, JsonSerializer.SerializeToElement(new { code, rollback = new { attempted, restored,
            status = attempted == false ? "not_needed" : restored == true ? "restored" : restored == false ? "failed" : "unknown" }, audit }));
    }
}
