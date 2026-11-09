using System.Globalization;
using System.Text.Json;
using Flamoris.Logging;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

public sealed class EditorHost : IMcpHost, IAsyncDisposable
{
    private readonly SemaphoreSlim lane = new(1);
    private readonly NativeEngine engine = new();
    private readonly Workspace workspace;
    private string documentToken = Guid.NewGuid().ToString("N");
    private readonly string runtimeId = Guid.NewGuid().ToString("N");
    public bool Dirty { get; private set; }
    public RenderJobs Renders { get; }
    public McpBoundary Boundary { get; }
    public HostSnapshot Snapshot => new("flamoris.cantabile", "0.1.0", runtimeId, documentToken, engine.Revision);
    public event Action? Invalidating;
    public EditorHost(string directory)
    {
        try
        {
            workspace = new Workspace(directory); Renders = new RenderJobs(workspace);
            var saved = workspace.ReadProject(workspace.ActiveName);
            if (saved is { } loaded)
            {
                engine.Call(new { op = "project.load", expected_revision = engine.Revision, data = loaded.Project });
                workspace.Opened(workspace.ActiveName, loaded.Digest);
            }
            // No console sink: stdout belongs exclusively to the MCP protocol in stdio mode.
            var logOptions = new LoggingOptions { Level = "info", Outputs = [new LogOutputOptions { Type = "file", Path = "logs/mcp.log", Rotation = new LogRotationOptions { MaxFileSizeMb = 2, MaxFiles = 3 } }] };
            Boundary = new(this, Tools(), new McpOptions { ApplicationLifetime = true, MaxRequestBytes = 4 * 1024 * 1024 },
                new McpDiagnostics(FlamorisLogger.Create(logOptions, basePath: directory)));
        }
        catch { engine.Dispose(); workspace?.Dispose(); throw; }
    }
    public async Task<T> InvokeAsync<T>(Func<T> action, CancellationToken cancellationToken)
    {
        await lane.WaitAsync(cancellationToken);
        try { cancellationToken.ThrowIfCancellationRequested(); return action(); }
        catch (Exception e) when (e is ArgumentException or JsonException or FormatException or OverflowException or KeyNotFoundException) { throw new McpFault(McpErrors.InvalidRequest); }
        finally { lane.Release(); }
    }
    private JsonElement Get()
    {
        var value = engine.Call(new { op = "project.get" });
        if (System.Text.Encoding.UTF8.GetByteCount(value.GetProperty("project").GetRawText()) > Workspace.MaximumProjectBytes) throw new McpFault("response_too_large");
        return value;
    }
    private JsonElement Changed(object command, bool replace = false)
    {
        var result = engine.Call(command); Dirty = true;
        if (replace) documentToken = Guid.NewGuid().ToString("N");
        return result;
    }
    private JsonElement Save(string? name)
    {
        var project = Get().GetProperty("project");
        var result = workspace.Save(project, name); Dirty = false;
        return JsonSerializer.SerializeToElement(result);
    }
    private byte[] Midi(bool playback = false)
    {
        var value = engine.Call(new { op = "midi.export", playback });
        var bytes = Convert.FromBase64String(value.GetProperty("data_base64").GetString()!);
        if (bytes.Length > Workspace.MaximumMidiBytes) throw new McpFault("midi_too_large");
        return bytes;
    }
    private static JsonElement Schema(object properties, string[]? required = null) => JsonSerializer.SerializeToElement(new { type = "object", properties, required = required ?? [], additionalProperties = false });
    private static JsonElement EmptySchema => Schema(new { });
    private static JsonElement Decode(JsonElement input, params string[] allowed)
    {
        if (input.ValueKind != JsonValueKind.Object || input.EnumerateObject().Any(p => !allowed.Contains(p.Name))) throw new ArgumentException("Unexpected input field.");
        return input.Clone();
    }
    private static JsonElement Field(JsonElement input, string name) => input.TryGetProperty(name, out var value) ? value : throw new ArgumentException("Required input field is missing.");
    private static string Text(JsonElement input, string name, int maximum = 128)
    {
        var value = Field(input, name).GetString();
        if (string.IsNullOrWhiteSpace(value) || value.Length > maximum) throw new ArgumentException("Invalid text field.");
        return value;
    }
    private static JsonElement BoundedProject(JsonElement input)
    {
        Decode(input, "data"); var data = Field(input, "data");
        if (data.ValueKind != JsonValueKind.Object || System.Text.Encoding.UTF8.GetByteCount(data.GetRawText()) > Workspace.MaximumProjectBytes) throw new ArgumentException("Invalid project.");
        return data.Clone();
    }
    private static JsonElement Commands(JsonElement input)
    {
        Decode(input, "commands"); var value = Field(input, "commands");
        if (value.ValueKind != JsonValueKind.Array || value.GetArrayLength() is < 1 or > 256) throw new ArgumentException("Invalid batch size.");
        // The native core's closed command decoder performs domain validation and atomic commit.
        foreach (var command in value.EnumerateArray()) if (command.ValueKind != JsonValueKind.Object || !command.TryGetProperty("op", out _)) throw new ArgumentException("Invalid command.");
        return value.Clone();
    }
    public IEnumerable<HostTool> Tools()
    {
        yield return new HostTool<JsonElement>("project_get", "Read the authoritative native project, revision and shared history.", EmptySchema,
            OperationKind.Query, x => Decode(x), (context, _, _) => context.ReadAsync(Get), foreground: false);
        yield return new HostTool<JsonElement>("project_load", "Replace the current project with validated project data. Unsaved edits require an explicit discard flag.",
            Schema(new { data = new { type = "object" }, discard_unsaved = new { type = "boolean" } }, ["data", "discard_unsaved"]), OperationKind.Transaction,
            x => { Decode(x, "data", "discard_unsaved"); if (Field(x, "data").ValueKind != JsonValueKind.Object || System.Text.Encoding.UTF8.GetByteCount(Field(x, "data").GetRawText()) > Workspace.MaximumProjectBytes) throw new ArgumentException(); _ = Field(x, "discard_unsaved").GetBoolean(); return x; },
            (ctx, x, _) => ctx.CommitAsync(() => { DemandDiscard(x); return Changed(new { op = "project.load", expected_revision = engine.Revision, data = Field(x, "data") }, true); }));
        yield return new HostTool<JsonElement>("edit_batch", "Atomically execute the native core's typed MIDI edit commands. Uses the current MCP revision guard.",
            Schema(new { commands = new { type = "array", minItems = 1, maxItems = 256, items = new { type = "object" } } }, ["commands"]), OperationKind.Transaction, Commands,
            (ctx, commands, _) => ctx.CommitAsync(() => Changed(new { op = "edit.batch", expected_revision = engine.Revision, commands })));
        yield return new HostTool<JsonElement>("history_undo", "Undo through the native editor's shared history.", EmptySchema, OperationKind.Undo,
            x => Decode(x), (ctx, _, _) => ctx.CommitAsync(() => Changed(new { op = "history.undo", expected_revision = engine.Revision })));
        yield return new HostTool<JsonElement>("history_redo", "Redo through the native editor's shared history.", EmptySchema, OperationKind.Redo,
            x => Decode(x), (ctx, _, _) => ctx.CommitAsync(() => Changed(new { op = "history.redo", expected_revision = engine.Revision })));
        yield return new HostTool<JsonElement>("project_list", "List saved Cantabile projects in the configured workspace.", EmptySchema, OperationKind.Query,
            x => Decode(x), (ctx, _, _) => ctx.ReadAsync(() => JsonSerializer.SerializeToElement(workspace.ListProjects())), foreground: false);
        yield return new HostTool<JsonElement>("project_save", "Atomically save the current project, or Save As a new workspace name ending .cantabile.json.",
            Schema(new { name = new { type = "string", maxLength = 128 } }), OperationKind.Transaction, x => Decode(x, "name"),
            (ctx, x, _) => ctx.CommitAsync(() => Save(x.TryGetProperty("name", out var nameValue) ? Text(x, "name") : null)));
        yield return new HostTool<JsonElement>("project_open", "Open a saved workspace project; requires an explicit discard flag for unsaved edits.",
            Schema(new { name = new { type = "string", maxLength = 128 }, discard_unsaved = new { type = "boolean" } }, ["name", "discard_unsaved"]), OperationKind.Transaction,
            x => { Decode(x, "name", "discard_unsaved"); _ = Text(x, "name"); _ = Field(x, "discard_unsaved").GetBoolean(); return x; },
            (ctx, x, _) => ctx.CommitAsync(() => {
                DemandDiscard(x); string name = Text(x, "name"); var loaded = workspace.ReadProject(name) ?? throw new McpFault("file_not_found");
                var result = Changed(new { op = "project.load", expected_revision = engine.Revision, data = loaded.Project }, true);
                workspace.Opened(name, loaded.Digest); Dirty = false; return result;
            }));
        yield return new HostTool<JsonElement>("midi_import", "Import at most 1 MiB of base64 SMF data through the native MIDI parser; requires explicit unsaved-edit handling.",
            Schema(new { data_base64 = new { type = "string", maxLength = 1398104 }, discard_unsaved = new { type = "boolean" } }, ["data_base64", "discard_unsaved"]), OperationKind.Transaction,
            x => { Decode(x, "data_base64", "discard_unsaved"); string data = Text(x, "data_base64", 1398104); if (Convert.FromBase64String(data).Length > Workspace.MaximumMidiBytes) throw new ArgumentException(); _ = Field(x, "discard_unsaved").GetBoolean(); return x; },
            (ctx, x, _) => ctx.CommitAsync(() => { DemandDiscard(x); return Changed(new { op = "midi.import", expected_revision = engine.Revision, data_base64 = Field(x, "data_base64").GetString() }, true); }));
        yield return new HostTool<JsonElement>("midi_export", "Export the current native project as an immutable MIDI artifact. Read bytes with file_read.", EmptySchema,
            OperationKind.Transaction, x => Decode(x), (ctx, _, _) => ctx.CommitAsync(() => workspace.PublishBytes(Midi(), ".mid", "audio/midi", engine.Revision)));
        yield return new HostTool<JsonElement>("file_read", "Read up to 128 KiB of one session-owned exported file with per-chunk and whole-file SHA-256.",
            Schema(new { file_id = new { type = "string", maxLength = 32 }, offset = new { type = "integer", minimum = 0 }, max_bytes = new { type = "integer", minimum = 1, maximum = Workspace.MaximumChunkBytes } }, ["file_id", "offset"]), OperationKind.Query,
            x => { Decode(x, "file_id", "offset", "max_bytes"); _ = Text(x, "file_id", 32); _ = Field(x, "offset").GetInt64(); return x; },
            (ctx, x, _) => ctx.ReadAsync(() => workspace.ReadArtifact(Text(x, "file_id", 32), Field(x, "offset").GetInt64(), x.TryGetProperty("max_bytes", out var max) ? max.GetInt32() : Workspace.MaximumChunkBytes)), foreground: false);
        yield return new HostTool<JsonElement>("file_list", "List immutable exports retained in this workspace, including recovered exports after restart.", EmptySchema,
            OperationKind.Query, x => Decode(x), (ctx, _, _) => ctx.ReadAsync(workspace.ListArtifacts), foreground: false);
        yield return new HostTool<JsonElement>("file_release", "Permanently release one exported artifact and its metadata to reclaim the bounded export quota. Never deletes projects.",
            Schema(new { file_id = new { type = "string", maxLength = 32 } }, ["file_id"]), OperationKind.Mutation,
            x => { Decode(x, "file_id"); _ = Text(x, "file_id", 32); return x; }, (ctx, x, _) => ctx.CommitAsync(() => workspace.ReleaseArtifact(Text(x, "file_id", 32))));
        yield return new HostTool<JsonElement>("instruments_list", "Report actually configured render backends. VST 3 is not implemented in this first slice.", EmptySchema,
            OperationKind.Query, x => Decode(x), (ctx, _, _) => ctx.ReadAsync(() => JsonSerializer.SerializeToElement(Renders.Instruments())), foreground: false);
        yield return new HostTool<JsonElement>("audio_render", "Snapshot the current project to a bounded offline FluidSynth WAV job. VST instrument requests fail explicitly.",
            Schema(new { max_seconds = new { type = "integer", minimum = 1, maximum = 600 } }), OperationKind.Transaction, x => Decode(x, "max_seconds"),
            (ctx, x, _) => ctx.CommitAsync(() => {
                var project = Get().GetProperty("project");
                foreach (var track in project.GetProperty("tracks").EnumerateArray())
                    if (track.TryGetProperty("instrument", out var instrument) && instrument.ValueKind == JsonValueKind.Object && instrument.TryGetProperty("backend", out var backend)
                        && backend.GetString() != "fluidsynth") throw new McpFault("unsupported_instrument_backend");
                foreach (var track in project.GetProperty("tracks").EnumerateArray())
                    if (track.TryGetProperty("instrument", out var settings) && settings.ValueKind == JsonValueKind.Object && settings.EnumerateObject().Any(p => p.Name != "backend")) throw new McpFault("unsupported_instrument_settings");
                return Renders.Begin(Midi(playback: true), engine.Revision, x.TryGetProperty("max_seconds", out var max) ? max.GetInt32() : 600);
            }));
        yield return new HostTool<JsonElement>("audio_status", "Read render job state and complete immutable WAV metadata.", Schema(new { job_id = new { type = "string", maxLength = 32 } }, ["job_id"]), OperationKind.Query,
            x => { Decode(x, "job_id"); _ = Text(x, "job_id", 32); return x; }, (ctx, x, _) => ctx.ReadAsync(() => Renders.Status(Text(x, "job_id", 32))), foreground: false);
        yield return new HostTool<JsonElement>("audio_cancel", "Cancel one current host-owned audio render job.", Schema(new { job_id = new { type = "string", maxLength = 32 } }, ["job_id"]), OperationKind.Mutation,
            x => { Decode(x, "job_id"); _ = Text(x, "job_id", 32); return x; }, (ctx, x, _) => ctx.CommitAsync(() => Renders.Cancel(Text(x, "job_id", 32))));
    }
    private void DemandDiscard(JsonElement input) { if (Dirty && !Field(input, "discard_unsaved").GetBoolean()) throw new McpFault("unsaved_changes"); }
    public RequestGuard CurrentGuard() => new(runtimeId, documentToken, engine.Revision);
    public async ValueTask DisposeAsync()
    {
        Invalidating?.Invoke(); Boundary.Dispose(); await Renders.DisposeAsync();
        await lane.WaitAsync(); try { engine.Dispose(); workspace.Dispose(); } finally { lane.Release(); lane.Dispose(); }
    }
}
