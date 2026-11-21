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
    public static JsonElement InspectInput(JsonElement input) { Object(input, "track_id"); ValidateTrack(input); return input.Clone(); }
    private static void ValidateTrack(JsonElement input)
    {
        if (input.TryGetProperty("track_id", out var track) && (track.ValueKind != JsonValueKind.String || track.GetString() is not { Length: > 0 and <= 128 })) throw new ArgumentException("Invalid track.");
    }
    public static JsonElement ScriptInput(JsonElement input, bool write)
    {
        try { return ValidateScriptInput(input, write); }
        catch (Exception e) when (e is InvalidOperationException or KeyNotFoundException or FormatException or OverflowException)
        { throw new ArgumentException("Invalid typed VST input."); }
    }
    private static JsonElement ValidateScriptInput(JsonElement input, bool write)
    {
        Object(input, write ? ["script", "files", "track_id"] : ["script", "track_id"]); ValidateTrack(input);
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

    public JsonElement Inspect(CancellationToken token, VstCatalog.Entry? target = null, byte[]? state = null)
    {
        try { return ExecuteAsync(null, false, null, -1, token, target, state).GetAwaiter().GetResult(); }
        catch (WorkerFailure failure) { throw new McpFault(failure.Code); }
    }
    public JsonElement Script(JsonElement input, bool write, byte[]? midi, long revision, CancellationToken token, VstCatalog.Entry? target = null, byte[]? state = null)
    {
        try { return ExecuteAsync(input, write, midi, revision, token, target, state).GetAwaiter().GetResult(); }
        catch (WorkerFailure failure) { throw new McpFault(failure.Code); }
    }

    public async Task<JsonElement> ExecuteAsync(JsonElement? input, bool write, byte[]? midi, long revision, CancellationToken token,
        VstCatalog.Entry? target = null, byte[]? state = null)
    {
        string tool = input is null ? "vst_inspect" : write ? "vst_script_write" : "vst_script";
        int operations = input?.GetProperty("script").GetProperty("operations").GetArrayLength() ?? 0;
        string? sandbox = null;
        var published = new List<JsonElement>();
        var pendingFiles = new List<(string Path, string Extension, string Mime)>();
        int timeout = 30000;
        if (input is { } request && request.GetProperty("script").TryGetProperty("limits", out var limits) && limits.TryGetProperty("timeout_ms", out var time)) timeout = time.GetInt32();
        using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(token);
        lifetime.CancelAfter(timeout);
        try
        {
            string? selectedModule = target?.Path ?? module, selectedClass = target?.ClassId ?? classId;
            string? selectedPreset = target is null ? preset : null;
            if (write && !writeEnabled) throw new McpFault("vst_write_disabled");
            if (!File.Exists(renderer) || string.IsNullOrEmpty(selectedModule) || (!File.Exists(selectedModule) && !Directory.Exists(selectedModule)) || (!string.IsNullOrEmpty(selectedPreset) && !File.Exists(selectedPreset))) throw new McpFault("vst_backend_unavailable");
            lifetime.Token.ThrowIfCancellationRequested();
            sandbox = workspace.CreateVstSandbox();
            if (state is not null)
            {
                selectedPreset = Path.Combine(sandbox, ".instance.vstpreset");
                await File.WriteAllBytesAsync(selectedPreset, state, lifetime.Token);
            }
            var args = new List<string>();
            args.Add(input is null ? "inspect" : "script"); args.Add("--plugin"); args.Add(Path.GetFullPath(selectedModule));
            if (!string.IsNullOrEmpty(selectedPreset)) { args.Add("--preset"); args.Add(Path.GetFullPath(selectedPreset)); }
            if (!string.IsNullOrEmpty(selectedClass)) { args.Add("--class-id"); args.Add(selectedClass); }
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
            var response = await WorkerProcess.Run(renderer!, args, sandbox, TimeSpan.FromMilliseconds(timeout), lifetime.Token, 8 * 1024 * 1024);
            var result = JsonNode.Parse(response.GetRawText())!.AsObject();
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
            foreach (var output in outputs)
            {
                lifetime.Token.ThrowIfCancellationRequested();
                string pending = workspace.TempFile(output.Extension);
                pendingFiles.Add((pending, output.Extension, output.Kind == "wav" ? "audio/wav" : "application/x-vstpreset"));
                File.Copy(output.Source, pending, overwrite: false);
            }
            lifetime.Token.ThrowIfCancellationRequested();
            workspace.VstAudit(tool, "completed", operations);
            published.AddRange(workspace.PublishBatch(pendingFiles, revision, lifetime.Token));
            var artifacts = new JsonArray();
            for (int index = 0; index < outputs.Count; ++index)
            {
                var artifact = JsonNode.Parse(published[index].GetRawText())!.AsObject();
                artifact["name"] = outputs[index].Name; artifact["kind"] = outputs[index].Kind; artifacts.Add(artifact);
            }
            result["artifacts"] = artifacts;
            return JsonSerializer.SerializeToElement(result);
        }
        catch (Exception e)
        {
            foreach (var artifact in published)
                try { workspace.ReleaseArtifact(artifact.GetProperty("file_id").GetString()!); } catch { }
            bool timedOut = e is OperationCanceledException && lifetime.IsCancellationRequested && !token.IsCancellationRequested;
            try { workspace.VstAudit(tool, e is McpFault fault ? fault.Code : e is WorkerFailure workerFailure ? workerFailure.Code : timedOut ? "vst_timeout" : e is OperationCanceledException ? "cancelled" : "failed", operations); } catch { }
            if (timedOut) throw new McpFault("vst_timeout");
            throw;
        }
        finally
        {
            foreach (var pending in pendingFiles) File.Delete(pending.Path);
            if (sandbox is not null) Directory.Delete(sandbox, recursive: true);
        }
    }
}
