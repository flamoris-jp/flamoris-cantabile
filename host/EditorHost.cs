using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
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
    private VstController Vst { get; }
    private VstCatalog Catalog { get; }
    private AsyncJobs Jobs { get; } = new();
    private BandRenderer Band { get; }
    private SoundShelf Sounds { get; }
    public McpBoundary Boundary { get; }
    public HostSnapshot Snapshot => new("flamoris.cantabile", "0.1.0", runtimeId, documentToken, engine.Revision);
    public event Action? Invalidating;
    public EditorHost(string directory)
    {
        try
        {
            workspace = new Workspace(directory); Renders = new RenderJobs(workspace); Vst = new VstController(workspace);
            Catalog = new VstCatalog(workspace); Band = new BandRenderer(workspace, Catalog);
            Sounds = new SoundShelf(workspace, Catalog);
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
    private JsonElement Track(string id)
    {
        foreach (var track in Get().GetProperty("project").GetProperty("tracks").EnumerateArray())
            if (track.GetProperty("id").GetString() == id) return track.Clone();
        throw new McpFault("NOT_FOUND");
    }
    private (VstCatalog.Entry? Entry, byte[]? State, int PrerollMs) Target(JsonElement input)
    {
        if (!input.TryGetProperty("track_id", out _)) return (null, null, 0);
        var track = Track(Text(input, "track_id"));
        if (!track.TryGetProperty("instrument", out var instrument) || !instrument.TryGetProperty("catalog_id", out var id)) throw new McpFault("instrument_unassigned");
        var entry = Catalog.Resolve(id.GetString()!);
        if (instrument.TryGetProperty("plugin_version", out var version) && version.GetString() != entry.Version) throw new McpFault("sound_version_mismatch");
        byte[]? state = BandRenderer.State(instrument);
        if (state is not null) SoundShelf.ValidateState(state, entry.ClassId);
        return (entry, state, instrument.TryGetProperty("preroll_ms", out var preroll) ? preroll.GetInt32() : 0);
    }
    private byte[] TargetMidi(JsonElement input)
    {
        if (!input.TryGetProperty("track_id", out _)) return Midi(playback: true);
        var plan = engine.Call(new { op = "audio.plan", track_id = Text(input, "track_id") });
        return Convert.FromBase64String(plan.GetProperty("parts")[0].GetProperty("midi_base64").GetString()!);
    }
    private static JsonElement Identified(JsonElement result, JsonElement input)
    {
        if (!input.TryGetProperty("track_id", out var id)) return result;
        var value = JsonNode.Parse(result.GetRawText())!.AsObject(); value["track_id"] = id.GetString();
        return JsonSerializer.SerializeToElement(value);
    }
    private JsonElement UpdateInstrument(string track, object? instrument) => Changed(new { op = "edit.batch", expected_revision = engine.Revision,
        commands = new[] { new { op = "track.update", track_id = track, patch = new { instrument } } } });
    private JsonElement InstrumentState(JsonElement input)
    {
        string trackId = Text(input, "track_id"), fileId = Text(input, "file_id", 32);
        var target = Target(input); byte[] bytes = workspace.VstInputArtifact(fileId, ".vstpreset");
        SoundShelf.ValidateState(bytes, target.Entry!.ClassId);
        // Retain loading conditions, but a newly supplied state no longer has
        // the previously assigned shelf identity.
        var instrument = JsonNode.Parse(Track(trackId).GetProperty("instrument").GetRawText())!.AsObject();
        instrument.Remove("sound_id"); instrument["state_base64"] = Convert.ToBase64String(bytes);
        return UpdateInstrument(trackId, instrument);
    }
    private JsonElement RenderStatus(string id)
    {
        try { return Renders.Status(id); } catch (McpFault e) when (e.Code == "unknown_job") { return Jobs.Status(id); }
    }
    private JsonElement RenderCancel(string id)
    {
        try { return Renders.Cancel(id); } catch (McpFault e) when (e.Code == "unknown_job") { return Jobs.Cancel(id); }
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
        yield return new HostTool<JsonElement>("instruments_list", "Read configured legacy backends and the installed VST3 catalog. Detection, load/inspection and audibility are separate; module paths remain private to this host.", EmptySchema,
            OperationKind.Query, x => Decode(x), (ctx, _, _) => ctx.ReadAsync(() => {
                var result = JsonSerializer.SerializeToNode(Renders.Instruments())!.AsObject(); result["catalog"] = JsonNode.Parse(Catalog.List().GetRawText());
                result["render_selection"] = "track_catalog_if_any_assignment_else_legacy"; return JsonSerializer.SerializeToElement(result);
            }), foreground: false);
        yield return new HostTool<JsonElement>("audio_render", "Snapshot the native project to an asynchronous job. Catalog assignments render aligned float32 stems and a float64-summed PCM16 mix; any active unassigned/missing instrument fails explicitly. Projects without catalog assignments retain the legacy backend.",
            Schema(new { max_seconds = new { type = "integer", minimum = 1, maximum = 600 } }), OperationKind.Transaction, x => Decode(x, "max_seconds"),
            (ctx, x, _) => ctx.CommitAsync(() => {
                var project = Get().GetProperty("project");
                if (project.GetProperty("tracks").EnumerateArray().Any(t => t.TryGetProperty("instrument", out var i) && i.TryGetProperty("catalog_id", out var catalogId)))
                {
                    var plan = engine.Call(new { op = "audio.plan" }); long revision = engine.Revision;
                    int maximum = x.TryGetProperty("max_seconds", out var duration) ? duration.GetInt32() : 600;
                    if (maximum is < 1 or > 600) throw new ArgumentException();
                    return Jobs.Begin("band_render", revision, (progress, token) => Band.Run(Band.Prepare(plan), revision, maximum, progress, token));
                }
                foreach (var track in project.GetProperty("tracks").EnumerateArray())
                    if (track.TryGetProperty("instrument", out var instrument) && instrument.ValueKind == JsonValueKind.Object && instrument.TryGetProperty("backend", out var backend)
                        && backend.GetString() != Renders.Backend) throw new McpFault("unsupported_instrument_backend");
                foreach (var track in project.GetProperty("tracks").EnumerateArray())
                    if (track.TryGetProperty("instrument", out var settings) && settings.ValueKind == JsonValueKind.Object && settings.EnumerateObject().Any(p => p.Name != "backend")) throw new McpFault("unsupported_instrument_settings");
                return Renders.Begin(Midi(playback: true), engine.Revision, x.TryGetProperty("max_seconds", out var max) ? max.GetInt32() : 600);
            }));
        yield return new HostTool<JsonElement>("audio_status", "Read render job state and complete immutable WAV metadata.", Schema(new { job_id = new { type = "string", maxLength = 32 } }, ["job_id"]), OperationKind.Query,
            x => { Decode(x, "job_id"); _ = Text(x, "job_id", 32); return x; }, (ctx, x, _) => ctx.ReadAsync(() => RenderStatus(Text(x, "job_id", 32))), foreground: false);
        yield return new HostTool<JsonElement>("audio_cancel", "Cancel one current host-owned audio render job.", Schema(new { job_id = new { type = "string", maxLength = 32 } }, ["job_id"]), OperationKind.Mutation,
            x => { Decode(x, "job_id"); _ = Text(x, "job_id", 32); return x; }, (ctx, x, _) => ctx.CommitAsync(() => RenderCancel(Text(x, "job_id", 32))));
        yield return new HostTool<JsonElement>("vst_inspect", "Read the configured VST 3 instrument's public parameters, MIDI mappings, buses, programs and interfaces as JSON in an isolated bounded worker.",
            Schema(new { track_id = new { type = "string", maxLength = 128 } }), OperationKind.Query, VstController.InspectInput,
            (ctx, x, token) => ctx.ReadAsync(() => { var target = Target(x); return Identified(Vst.Inspect(token, target.Entry, target.State), x); }), foreground: false);
        yield return new HostTool<JsonElement>("vst_script", "Run a bounded version-1 JSON VST script with inspect, list_parameters and get_parameter only. No file bindings or writes.",
            Schema(new { script = new { type = "object" }, track_id = new { type = "string", maxLength = 128 } }, ["script"]), OperationKind.Query,
            x => VstController.ScriptInput(x, false), (ctx, x, token) => ctx.ReadAsync(() => { var target = Target(x); return Identified(Vst.Script(x, false, null, engine.Revision, token, target.Entry, target.State, target.PrerollMs), x); }), foreground: false);
        yield return new HostTool<JsonElement>("vst_script_write", "Run allowed VST parameter, MIDI/render and state operations after Inspector validation. Requires edit permission, current guard and host CANTABILE_VST3_SCRIPT_WRITE opt-in. Current project MIDI is input.mid; files binds existing MIDI/state artifact IDs to safe basenames. Outputs publish only on success.",
            Schema(new { script = new { type = "object" }, track_id = new { type = "string", maxLength = 128 }, files = new { type = "object", maxProperties = 16, additionalProperties = new { type = "string", pattern = "^[a-f0-9]{32}$" } } }, ["script"]), OperationKind.Transaction,
            x => VstController.ScriptInput(x, true), (ctx, x, token) => ctx.CommitAsync(() => { var target = Target(x); return Identified(Vst.Script(x, true, TargetMidi(x), engine.Revision, token, target.Entry, target.State, target.PrerollMs), x); }));
        yield return new HostTool<JsonElement>("vst_scan", "Start an asynchronous scan of standard VST3 locations and operator-registered CANTABILE_VST3_PATHS. One isolated worker per module/class; failures are retained. Poll vst_job_status.", EmptySchema,
            OperationKind.Mutation, x => Decode(x), (ctx, _, _) => ctx.CommitAsync(() => Jobs.Begin("vst_scan", engine.Revision, Catalog.Scan)));
        yield return new HostTool<JsonElement>("vst_job_status", "Read a host-owned scan, script or multi-instrument job, including bounded rollback/audit failure summaries and immutable completed artifacts.",
            Schema(new { job_id = new { type = "string", maxLength = 32 } }, ["job_id"]), OperationKind.Query,
            x => { Decode(x, "job_id"); _ = Text(x, "job_id", 32); return x; }, (ctx, x, _) => ctx.ReadAsync(() => Jobs.Status(Text(x, "job_id", 32))), foreground: false);
        yield return new HostTool<JsonElement>("vst_job_cancel", "Cancel a host-owned scan, script or multi-instrument job. An isolated worker's rollback is unknown after a hard kill.",
            Schema(new { job_id = new { type = "string", maxLength = 32 } }, ["job_id"]), OperationKind.Mutation,
            x => { Decode(x, "job_id"); _ = Text(x, "job_id", 32); return x; }, (ctx, x, _) => ctx.CommitAsync(() => Jobs.Cancel(Text(x, "job_id", 32))));
        yield return new HostTool<JsonElement>("instrument_assign", "Assign a catalog VST3 to a MIDI track, resetting its previous sound state. Each track is an independent instance. Uses native Undo/Redo; no host restart.",
            Schema(new { track_id = new { type = "string", maxLength = 128 }, catalog_id = new { type = "string", maxLength = 128 } }, ["track_id", "catalog_id"]), OperationKind.Transaction,
            x => { Decode(x, "track_id", "catalog_id"); _ = Text(x, "track_id"); _ = Text(x, "catalog_id"); return x; },
            (ctx, x, _) => ctx.CommitAsync(() => { var entry = Catalog.Resolve(Text(x, "catalog_id")); return UpdateInstrument(Text(x, "track_id"), new { backend = "vst3", catalog_id = entry.Id }); }));
        yield return new HostTool<JsonElement>("instrument_clear", "Remove a track's instrument assignment and stored state through native Undo/Redo.",
            Schema(new { track_id = new { type = "string", maxLength = 128 } }, ["track_id"]), OperationKind.Transaction,
            x => { Decode(x, "track_id"); _ = Text(x, "track_id"); return x; }, (ctx, x, _) => ctx.CommitAsync(() => UpdateInstrument(Text(x, "track_id"), null)));
        yield return new HostTool<JsonElement>("instrument_state_set", "Copy a completed standard state artifact into the native track instance. Checks class identity; saved projects embed state (max 1 MiB/instance within project quota). The next async render applies it explicitly.",
            Schema(new { track_id = new { type = "string", maxLength = 128 }, file_id = new { type = "string", maxLength = 32 } }, ["track_id", "file_id"]), OperationKind.Transaction,
            x => { Decode(x, "track_id", "file_id"); _ = Text(x, "track_id"); _ = Text(x, "file_id", 32); return x; }, (ctx, x, _) => ctx.CommitAsync(() => InstrumentState(x)));
        yield return new HostTool<JsonElement>("vst_script_begin", "Start a bounded asynchronous write script on a track instance (or legacy configured instrument). Requires SCRIPT_WRITE opt-in. Poll vst_job_status for outputs or typed rollback/audit summary; use instrument_state_set to retain a saved state.",
            Schema(new { script = new { type = "object" }, track_id = new { type = "string", maxLength = 128 }, files = new { type = "object", maxProperties = 16, additionalProperties = new { type = "string", pattern = "^[a-f0-9]{32}$" } } }, ["script"]), OperationKind.Transaction,
            x => VstController.ScriptInput(x, true), (ctx, x, _) => ctx.CommitAsync(() => {
                var target = Target(x); byte[] midi = TargetMidi(x); long revision = engine.Revision;
                return Jobs.Begin("vst_script", revision, async (progress, token) => {
                    progress(0, 1); var result = await Vst.ExecuteAsync(x, true, midi, revision, token, target.Entry, target.State, target.PrerollMs);
                    progress(1, 1); return Identified(result, x);
                });
            }));
        foreach (var tool in SoundTools()) yield return tool;
    }
    private static JsonElement SoundInput(Func<JsonElement, JsonElement> validate, JsonElement input)
    {
        try { return validate(input); }
        catch (Exception e) when (e is InvalidOperationException or KeyNotFoundException or FormatException or OverflowException) { throw new ArgumentException("Invalid sound input."); }
    }
    private IEnumerable<HostTool> SoundTools()
    {
        var text = new { type = "string", minLength = 1, maxLength = 128 };
        var labels = new { type = "array", maxItems = 16, items = new { type = "string", minLength = 1, maxLength = 64 } };
        yield return new HostTool<JsonElement>("sound_register", "Retain a private independent copy of a track's standard state or state artifact. Returns a stable sound ID; never exposes state bytes or local paths. Registration does not certify audible restoration.",
            Schema(new { track_id = text, file_id = new { type = "string", maxLength = 32 }, display_name = text, category = new { type = "string", minLength = 1, maxLength = 64 },
                roles = labels, tags = labels, classification_source = new { type = "string", minLength = 1, maxLength = 512 },
                warmup_ms = new { type = "integer", minimum = 0, maximum = 30000 }, realtime = new { type = "boolean" },
                preroll_ms = new { type = "integer", minimum = 0, maximum = 30000 } }, ["track_id", "display_name", "category", "classification_source"]),
            OperationKind.Transaction, x => SoundInput(SoundShelf.RegisterInput, x), (ctx, x, _) => ctx.CommitAsync(() => {
                var target = Target(x);
                byte[] state = x.TryGetProperty("file_id", out var file) ? workspace.VstInputArtifact(file.GetString()!, ".vstpreset") : target.State ?? throw new McpFault("sound_state_required");
                return Sounds.Register(x, target.Entry!, state);
            }));
        yield return new HostTool<JsonElement>("sound_search", "Search individual saved sounds by category, role, tag and verification. Returns availability, state digest and loading conditions, never paths or state payloads.",
            Schema(new { category = text, role = text, tag = text, verification = new { type = "string", @enum = new[] { "initial_audible", "state_captured", "restored_audible", "human_reviewed", "unverified" } },
                offset = new { type = "integer", minimum = 0, maximum = 128 }, limit = new { type = "integer", minimum = 1, maximum = 32 } }),
            OperationKind.Query, x => SoundInput(SoundShelf.SearchInput, x), (ctx, x, _) => ctx.ReadAsync(() => Sounds.Search(x)), foreground: false);
        yield return new HostTool<JsonElement>("sound_assign", "Atomically apply one saved sound and its loading conditions to a native track. One Undo/Redo entry; mixer settings remain independent. Unverified restoration is reported, never implied by assignment.",
            Schema(new { sound_id = text, track_id = text }, ["sound_id", "track_id"]), OperationKind.Transaction,
            x => { Decode(x, "sound_id", "track_id"); _ = Text(x, "sound_id"); _ = Text(x, "track_id"); return x; },
            (ctx, x, _) => ctx.CommitAsync(() => {
                var target = Sounds.Resolve(Text(x, "sound_id"));
                var result = UpdateInstrument(Text(x, "track_id"), SoundShelf.Instrument(target.Sound));
                return JsonSerializer.SerializeToElement(new { edit = result, sound_id = target.Sound.SoundId,
                    restoration = target.Sound.Verification.RestoredAudible ? "operator_verified" : "unverified" });
            }));
        yield return new HostTool<JsonElement>("sound_preview", "Render a saved state with original fixed melody, bass or drum notes in an isolated async job. Preserves the current project, revision and history. Poll audio_status/vst_job_status; cancel with audio_cancel/vst_job_cancel; retrieve mix/stem using file_read.",
            Schema(new { sound_id = text, phrase = new { type = "string", @enum = new[] { "melody", "bass", "drums" } } }, ["sound_id", "phrase"]), OperationKind.Transaction,
            x => { Decode(x, "sound_id", "phrase"); _ = Text(x, "sound_id"); if (Text(x, "phrase") is not ("melody" or "bass" or "drums")) throw new ArgumentException(); return x; },
            (ctx, x, _) => ctx.CommitAsync(() => {
                var target = Sounds.Resolve(Text(x, "sound_id")); byte[] midi = SoundShelf.PreviewMidi(Text(x, "phrase")); long revision = engine.Revision;
                var parts = new[] { new BandRenderer.Part("preview", target.Instrument, target.State, midi, 0.5, 0, target.Sound.WarmupMs, target.Sound.Realtime, target.Sound.PrerollMs) };
                return Jobs.Begin("sound_preview", revision, async (progress, token) => {
                    var result = await Band.Run(parts, revision, 10, progress, token);
                    return JsonSerializer.SerializeToElement(new { sound_id = target.Sound.SoundId, state_sha256 = target.Sound.StateSha256, render = result });
                });
            }));
        yield return new HostTool<JsonElement>("sound_verify", "Record explicit operator evidence separately for initial audibility, restored audibility and human review. These are attestations, not automatic tests or proof of preset identity.",
            Schema(new { sound_id = text, initial_audible = new { type = "boolean" }, restored_audible = new { type = "boolean" }, human_reviewed = new { type = "boolean" },
                evidence = new { type = "string", minLength = 1, maxLength = 1024 } }, ["sound_id", "initial_audible", "restored_audible", "human_reviewed", "evidence"]),
            OperationKind.Mutation, x => SoundInput(SoundShelf.VerifyInput, x), (ctx, x, _) => ctx.CommitAsync(() => Sounds.Verify(x)));
        yield return new HostTool<JsonElement>("sound_remove", "Delete one retained sound using its expected state SHA-256. Existing project copies and already-started previews retain their snapshots. Not undone by native project history.",
            Schema(new { sound_id = text, expected_sha256 = new { type = "string", minLength = 64, maxLength = 64 } }, ["sound_id", "expected_sha256"]), OperationKind.Mutation,
            x => { Decode(x, "sound_id", "expected_sha256"); _ = Text(x, "sound_id"); string hash = Text(x, "expected_sha256", 64); if (hash.Length != 64 || !hash.All(char.IsAsciiHexDigit)) throw new ArgumentException(); return x; },
            (ctx, x, _) => ctx.CommitAsync(() => Sounds.Remove(Text(x, "sound_id"), Text(x, "expected_sha256", 64))));
    }
    private void DemandDiscard(JsonElement input) { if (Dirty && !Field(input, "discard_unsaved").GetBoolean()) throw new McpFault("unsaved_changes"); }
    public RequestGuard CurrentGuard() => new(runtimeId, documentToken, engine.Revision);
    public async ValueTask DisposeAsync()
    {
        Invalidating?.Invoke(); Boundary.Dispose(); await Jobs.DisposeAsync(); await Renders.DisposeAsync();
        await lane.WaitAsync(); try { engine.Dispose(); workspace.Dispose(); } finally { lane.Release(); lane.Dispose(); }
    }
}
