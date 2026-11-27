using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

// One bounded process per plugin operation. Raw stdout/stderr never reaches MCP.
internal static class WorkerProcess
{
    private sealed class OutputLimit(string stream, int maximum, int observed) : Exception
    {
        public string Stream { get; } = stream;
        public int Maximum { get; } = maximum;
        public int Observed { get; } = observed;
    }
    public static async Task<JsonElement> Run(string executable, IEnumerable<string> arguments, string directory,
        TimeSpan timeout, CancellationToken token, int stdoutMaximum = 4 * 1024 * 1024, int stderrMaximum = 16384)
    {
        using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(token);
        lifetime.CancelAfter(timeout);
        using var process = new Process { StartInfo = new ProcessStartInfo(Path.GetFullPath(executable)) {
            WorkingDirectory = directory, RedirectStandardOutput = true, RedirectStandardError = true,
            UseShellExecute = false, CreateNoWindow = true } };
        foreach (var argument in arguments) process.StartInfo.ArgumentList.Add(argument);
        SafeEnvironment(process.StartInfo);
        if (!process.Start()) throw new McpFault("vst_start_failed");
        Task<string> output = Read(process.StandardOutput, "stdout", stdoutMaximum, lifetime.Token);
        Task<string> errors = Read(process.StandardError, "stderr", stderrMaximum, lifetime.Token, keepText: false);
        try
        {
            var pending = new List<Task> { process.WaitForExitAsync(lifetime.Token), output, errors };
            while (pending.Count > 0) { var done = await Task.WhenAny(pending); await done; pending.Remove(done); }
        }
        catch (Exception error)
        {
            bool exitedBeforeCleanup = process.HasExited, killRequested = false;
            try
            {
                if (!exitedBeforeCleanup) { process.Kill(entireProcessTree: true); killRequested = true; }
                await process.WaitForExitAsync(CancellationToken.None);
            }
            catch (InvalidOperationException) { }
            try { await Task.WhenAll(output, errors); } catch { }
            if (lifetime.IsCancellationRequested && !token.IsCancellationRequested) throw new McpFault("vst_timeout");
            if (error is OutputLimit limit)
                throw new WorkerFailure("vst_output_limit", JsonSerializer.SerializeToElement(new {
                    code = "vst_output_limit",
                    output_limit = new { stream = limit.Stream, maximum_characters = limit.Maximum,
                        observed_characters = limit.Observed },
                    termination = new { exited_before_cleanup = exitedBeforeCleanup, host_kill_requested = killRequested,
                        exit_code = process.HasExited ? (int?)process.ExitCode : null },
                    rollback = new { attempted = (bool?)null, restored = (bool?)null, status = "unknown" }
                }));
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
    private static async Task<string> Read(StreamReader stream, string name, int maximum, CancellationToken token, bool keepText = true)
    {
        var text = keepText ? new StringBuilder() : null;
        var buffer = new char[4096]; int count, observed = 0;
        while ((count = await stream.ReadAsync(buffer.AsMemory(), token)) > 0)
        {
            observed += count;
            if (observed > maximum) throw new OutputLimit(name, maximum, observed);
            text?.Append(buffer, 0, count);
        }
        return text?.ToString() ?? string.Empty;
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
        var summary = JsonSerializer.SerializeToNode(new { code, rollback = new { attempted, restored,
            status = attempted == false ? "not_needed" : restored == true ? "restored" : restored == false ? "failed" : "unknown" }, audit })!.AsObject();
        if (result.ValueKind == JsonValueKind.Object && result.TryGetProperty("error", out var failure) && failure.ValueKind == JsonValueKind.Object && failure.TryGetProperty("capture", out var capture) &&
            capture.ValueKind == JsonValueKind.Object && capture.TryGetProperty("unsupported_event_types", out var types) && types.ValueKind == JsonValueKind.Array &&
            capture.TryGetProperty("total_events", out var total) && total.ValueKind == JsonValueKind.Number && total.TryGetInt64(out long count) && count is >= 0 and <= 8193)
        {
            var safe = new JsonArray();
            foreach (var type in types.EnumerateArray().Take(64)) if (type.ValueKind == JsonValueKind.Number && type.TryGetInt32(out int number) && number is >= 0 and <= 65535) safe.Add(number);
            summary["capture"] = new JsonObject { ["unsupported_event_types"] = safe, ["total_events"] = count };
        }
        return new(code, JsonSerializer.SerializeToElement(summary));
    }
}
