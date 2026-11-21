using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

/// <summary>Product-owned, bounded VST worker adapter; never a general code executor.</summary>
public sealed partial class VstController(Workspace workspace)
{
    private const int MaximumScriptBytes = 256 * 1024;
    private const int MaximumStateBytes = 16 * 1024 * 1024;
    private readonly string? renderer = Environment.GetEnvironmentVariable("CANTABILE_RENDERER");
    private readonly string? module = Environment.GetEnvironmentVariable("CANTABILE_VST3_MODULE");
    private readonly string? preset = Environment.GetEnvironmentVariable("CANTABILE_VST3_PRESET");
    private readonly string? classId = Environment.GetEnvironmentVariable("CANTABILE_VST3_CLASS_ID");
    private readonly bool writeEnabled = Environment.GetEnvironmentVariable("CANTABILE_VST3_SCRIPT_WRITE")?.ToLowerInvariant() switch
    {
        null or "" or "false" or "0" => false,
        "true" or "1" => true,
        _ => throw new ArgumentException("Invalid VST script write setting.")
    };
    [GeneratedRegex("^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\\z", RegexOptions.CultureInvariant)]
    private static partial Regex SafeName();

    private static void Object(JsonElement value, params string[] fields)
    {
        if (value.ValueKind != JsonValueKind.Object) throw new ArgumentException("Expected object.");
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var p in value.EnumerateObject())
            if (!fields.Contains(p.Name) || !seen.Add(p.Name)) throw new ArgumentException("Unexpected field.");
    }
    public static JsonElement InspectInput(JsonElement input) { Object(input); return input.Clone(); }
    public static JsonElement ScriptInput(JsonElement input, bool write)
    {
        try { return ValidateScriptInput(input, write); }
        catch (Exception e) when (e is InvalidOperationException or KeyNotFoundException or FormatException or OverflowException)
        { throw new ArgumentException("Invalid typed VST input."); }
    }
    private static JsonElement ValidateScriptInput(JsonElement input, bool write)
    {
        Object(input, write ? ["script", "files"] : ["script"]);
        if (!input.TryGetProperty("script", out var script) || Encoding.UTF8.GetByteCount(script.GetRawText()) > MaximumScriptBytes)
            throw new ArgumentException("Missing or oversized script.");
        Object(script, "version", "operations", "limits");
        if (script.GetProperty("version").GetInt32() != 1) throw new ArgumentException("Unsupported DSL version.");
        var operations = script.GetProperty("operations");
        if (operations.ValueKind != JsonValueKind.Array || operations.GetArrayLength() is < 1 or > 256) throw new ArgumentException("Invalid operation count.");
        foreach (var operation in operations.EnumerateArray())
        {
            if (operation.ValueKind != JsonValueKind.Object || !operation.TryGetProperty("op", out var op)) throw new ArgumentException("Invalid operation.");
            var name = op.GetString();
            if (name is not ("inspect" or "list_parameters" or "get_parameter" or "set_parameter" or "midi" or "render" or "save_state" or "load_state")) throw new ArgumentException("Unknown operation.");
            if (!write && name is not ("inspect" or "list_parameters" or "get_parameter")) throw new McpFault("vst_write_required");
        }
        if (script.TryGetProperty("limits", out var limits))
        {
            Object(limits, "max_operations", "timeout_ms");
            if (limits.TryGetProperty("max_operations", out var count) && (count.GetInt32() is < 1 or > 256 || count.GetInt32() < operations.GetArrayLength())) throw new ArgumentException("Operation limit exceeded.");
            if (limits.TryGetProperty("timeout_ms", out var time) && time.GetInt32() is < 1 or > 120000) throw new ArgumentException("Invalid timeout.");
        }
        if (input.TryGetProperty("files", out var files))
        {
            if (files.ValueKind != JsonValueKind.Object) throw new ArgumentException("Invalid files.");
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var file in files.EnumerateObject())
            {
                string extension = Path.GetExtension(file.Name);
                if (!seen.Add(file.Name) || seen.Count > 16 || !ValidName(file.Name) || extension is not (".mid" or ".vstpreset") || file.Value.GetString() is not { Length: 32 } id || !Guid.TryParseExact(id, "N", out _)) throw new ArgumentException("Invalid file binding.");
            }
        }
        return input.Clone();
    }
    private static bool ValidName(string name)
    {
        if (!SafeName().IsMatch(name) || name.Contains("..", StringComparison.Ordinal) || name.EndsWith('.')) return false;
        string stem = name.Split('.')[0].ToUpperInvariant();
        return stem is not ("CON" or "PRN" or "AUX" or "NUL") &&
            !(stem.Length == 4 && (stem.StartsWith("COM", StringComparison.Ordinal) || stem.StartsWith("LPT", StringComparison.Ordinal)) && stem[3] is >= '1' and <= '9');
    }

    public JsonElement Inspect(CancellationToken token) => ExecuteAsync(null, false, null, -1, token).GetAwaiter().GetResult();
    public JsonElement Script(JsonElement input, bool write, byte[]? midi, long revision, CancellationToken token)
        => ExecuteAsync(input, write, midi, revision, token).GetAwaiter().GetResult();

    private async Task<JsonElement> ExecuteAsync(JsonElement? input, bool write, byte[]? midi, long revision, CancellationToken token)
    {
        string tool = input is null ? "vst_inspect" : write ? "vst_script_write" : "vst_script";
        int operations = input?.GetProperty("script").GetProperty("operations").GetArrayLength() ?? 0;
        string? sandbox = null;
        var published = new List<JsonElement>();
        int timeout = 30000;
        if (input is { } request && request.GetProperty("script").TryGetProperty("limits", out var limits) && limits.TryGetProperty("timeout_ms", out var time)) timeout = time.GetInt32();
        using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(token);
        lifetime.CancelAfter(timeout);
        try
        {
            if (write && !writeEnabled) throw new McpFault("vst_write_disabled");
            if (!File.Exists(renderer) || string.IsNullOrEmpty(module) || (!File.Exists(module) && !Directory.Exists(module)) || (!string.IsNullOrEmpty(preset) && !File.Exists(preset))) throw new McpFault("vst_backend_unavailable");
            lifetime.Token.ThrowIfCancellationRequested();
            sandbox = workspace.CreateVstSandbox();
            using var process = new Process { StartInfo = new ProcessStartInfo(Path.GetFullPath(renderer!))
            {
                RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false, CreateNoWindow = true,
                WorkingDirectory = sandbox
            } };
            var args = process.StartInfo.ArgumentList;
            args.Add(input is null ? "inspect" : "script"); args.Add("--plugin"); args.Add(Path.GetFullPath(module));
            if (!string.IsNullOrEmpty(preset)) { args.Add("--preset"); args.Add(Path.GetFullPath(preset)); }
            if (!string.IsNullOrEmpty(classId)) { args.Add("--class-id"); args.Add(classId); }
            if (input is { } scriptInput)
            {
                if (scriptInput.TryGetProperty("files", out var bindings))
                    foreach (var binding in bindings.EnumerateObject())
                    {
                        byte[] bytes = workspace.VstInputArtifact(binding.Value.GetString()!, Path.GetExtension(binding.Name));
                        if (Path.GetExtension(binding.Name) == ".vstpreset" && bytes.Length > MaximumStateBytes) throw new McpFault("file_too_large");
                        await File.WriteAllBytesAsync(Path.Combine(sandbox, binding.Name), bytes, lifetime.Token);
                    }
                if (write && midi is not null && !File.Exists(Path.Combine(sandbox, "input.mid")))
                    await File.WriteAllBytesAsync(Path.Combine(sandbox, "input.mid"), midi, lifetime.Token);
                string requestFile = Path.Combine(sandbox, ".request.json");
                await File.WriteAllTextAsync(requestFile, scriptInput.GetProperty("script").GetRawText(), lifetime.Token);
                args.Add("--script"); args.Add(requestFile); args.Add("--workspace"); args.Add(sandbox);
                if (write) args.Add("--allow-write");
                if (Environment.GetEnvironmentVariable("CANTABILE_VST3_REALTIME")?.ToLowerInvariant() is "true" or "1") args.Add("--realtime");
                string? warmup = Environment.GetEnvironmentVariable("CANTABILE_VST3_WARMUP_MS");
                if (!string.IsNullOrEmpty(warmup)) { args.Add("--warmup-ms"); args.Add(warmup); }
            }
            SafeEnvironment(process.StartInfo);
            lifetime.Token.ThrowIfCancellationRequested();
            if (!process.Start()) throw new McpFault("vst_start_failed");
            Task<string> stdout = ReadTextAsync(process.StandardOutput, 8 * 1024 * 1024, lifetime.Token);
            Task<string> stderr = ReadTextAsync(process.StandardError, 16384, lifetime.Token);
            try
            {
                var pending = new List<Task> { process.WaitForExitAsync(lifetime.Token), stdout, stderr };
                while (pending.Count != 0) { var done = await Task.WhenAny(pending).ConfigureAwait(false); await done.ConfigureAwait(false); pending.Remove(done); }
            }
            catch
            {
                try { process.Kill(entireProcessTree: true); await process.WaitForExitAsync(CancellationToken.None).ConfigureAwait(false); } catch (InvalidOperationException) { }
                try { await Task.WhenAll(stdout, stderr).ConfigureAwait(false); } catch { }
                if (lifetime.IsCancellationRequested && !token.IsCancellationRequested) throw new McpFault("vst_timeout");
                throw;
            }
            lifetime.Token.ThrowIfCancellationRequested();
            JsonObject result;
            try { result = JsonNode.Parse(await stdout.ConfigureAwait(false), documentOptions: new() { MaxDepth = 32 })!.AsObject(); }
            catch (Exception e) when (e is JsonException or InvalidOperationException or NullReferenceException) { throw new McpFault("vst_worker_crashed"); }
            if (process.ExitCode != 0 || result["ok"]?.GetValue<bool>() != true)
            {
                string? code = result["error"]?["code"]?.GetValue<string>();
                throw new McpFault(code is { Length: > 0 and <= 80 } && code.All(c => char.IsAsciiLetterOrDigit(c) || c == '_') ? code : "vst_worker_failed");
            }
            if (input is not null && result["phase1_passed"]?.GetValue<bool>() != true) throw new McpFault("vst_phase1_required");
            var outputs = new List<(string Name, string Kind, string Source, string Extension)>();
            if (result["files"] is JsonArray files)
            {
                if (!write && files.Count != 0) throw new McpFault("vst_write_required");
                var names = new HashSet<string>(StringComparer.Ordinal);
                foreach (var file in files)
                {
                    string name = file?["name"]?.GetValue<string>() ?? "", kind = file?["kind"]?.GetValue<string>() ?? "";
                    string extension = kind switch { "wav" => ".wav", "state" => ".vstpreset", _ => throw new McpFault("vst_invalid_output") };
                    if (!ValidName(name) || Path.GetExtension(name) != extension || !names.Add(name) || names.Count > 64) throw new McpFault("vst_invalid_output");
                    string source = Path.Combine(sandbox, name); Workspace.VerifyParents(sandbox); Workspace.DemandRegular(source);
                    var info = new FileInfo(source);
                    if (!info.Exists || info.Length > (kind == "state" ? MaximumStateBytes : Workspace.MaximumArtifactBytes)) throw new McpFault("vst_invalid_output");
                    outputs.Add((name, kind, source, extension));
                }
            }
            var artifacts = new JsonArray();
            foreach (var output in outputs)
            {
                lifetime.Token.ThrowIfCancellationRequested();
                string pending = workspace.TempFile(output.Extension);
                try
                {
                    File.Copy(output.Source, pending, overwrite: false);
                    lifetime.Token.ThrowIfCancellationRequested();
                    var metadata = workspace.PublishFile(pending, output.Extension, output.Kind == "wav" ? "audio/wav" : "application/x-vstpreset", revision);
                    published.Add(metadata);
                    lifetime.Token.ThrowIfCancellationRequested();
                    var artifact = JsonNode.Parse(metadata.GetRawText())!.AsObject(); artifact["name"] = output.Name; artifact["kind"] = output.Kind; artifacts.Add(artifact);
                }
                finally { File.Delete(pending); }
            }
            result["artifacts"] = artifacts;
            lifetime.Token.ThrowIfCancellationRequested();
            workspace.VstAudit(tool, "completed", operations);
            lifetime.Token.ThrowIfCancellationRequested();
            return JsonSerializer.SerializeToElement(result);
        }
        catch (Exception e)
        {
            foreach (var artifact in published)
                try { workspace.ReleaseArtifact(artifact.GetProperty("file_id").GetString()!); } catch { }
            bool timedOut = e is OperationCanceledException && lifetime.IsCancellationRequested && !token.IsCancellationRequested;
            try { workspace.VstAudit(tool, e is McpFault fault ? fault.Code : timedOut ? "vst_timeout" : e is OperationCanceledException ? "cancelled" : "failed", operations); } catch { }
            if (timedOut) throw new McpFault("vst_timeout");
            throw;
        }
        finally
        {
            if (sandbox is not null) Directory.Delete(sandbox, recursive: true);
        }
    }
    private static async Task<string> ReadTextAsync(StreamReader reader, int maximum, CancellationToken token)
    {
        var result = new StringBuilder(); char[] buffer = new char[4096]; int count;
        while ((count = await reader.ReadAsync(buffer.AsMemory(), token).ConfigureAwait(false)) > 0)
        {
            if (result.Length + count > maximum) throw new McpFault("vst_output_limit");
            result.Append(buffer, 0, count);
        }
        return result.ToString();
    }
    private static void SafeEnvironment(ProcessStartInfo info)
    {
        string[] retained = ["PATH", "LD_LIBRARY_PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL"];
        if (OperatingSystem.IsWindows()) retained = [..retained, "APPDATA", "LOCALAPPDATA", "USERPROFILE", "PROGRAMDATA", "PUBLIC", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432", "SYSTEMDRIVE"];
        var safe = info.Environment.Where(p => retained.Contains(p.Key, StringComparer.OrdinalIgnoreCase)).ToArray();
        info.Environment.Clear(); foreach (var entry in safe) info.Environment[entry.Key] = entry.Value;
    }
}
