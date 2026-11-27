using System.Text.Json;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

// A fresh visible plugin editor, never an existing DAW/standalone instance.
// Generated fixed Lua uses the vendor's documented load APIs; no caller Lua.
internal sealed class KontaktPreparation(Workspace workspace)
{
    private readonly string? worker = Environment.GetEnvironmentVariable("CANTABILE_RENDERER");
    private readonly bool enabled = Environment.GetEnvironmentVariable("CANTABILE_KONTAKT_PREPARE")?.ToLowerInvariant() is "true" or "1";
    private static int DialogWaitSeconds()
    {
        string? value = Environment.GetEnvironmentVariable("CANTABILE_KONTAKT_DIALOG_WAIT_SECONDS");
        if (value is null) return 20;
        if (!int.TryParse(value, System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out int seconds) || seconds < 1 || seconds > 90)
            throw new McpFault("kontakt_dialog_wait_invalid");
        return seconds;
    }
    internal void DemandEnabled()
    {
        if (!enabled) throw new McpFault("kontakt_prepare_disabled");
        if (!OperatingSystem.IsWindows()) throw new McpFault("kontakt_platform_unsupported");
        if (!File.Exists(worker)) throw new McpFault("vst_backend_unavailable");
        _ = DialogWaitSeconds();
    }
    internal static void DemandKontakt(VstCatalog.Entry target)
    {
        if (target.Vendor != "Native Instruments" || target.Name is not ("Kontakt 7" or "Kontakt 8")) throw new McpFault("kontakt_instrument_required");
    }
    internal async Task<JsonElement> Run(KontaktLibrary.Selection selection, VstCatalog.Entry target, long revision,
        Action<int, int> progress, CancellationToken token)
    {
        DemandEnabled(); DemandKontakt(target);
        int dialogWaitSeconds = DialogWaitSeconds();
        string sandbox = workspace.CreateVstSandbox(), pending = workspace.TempFile(".vstpreset");
        try
        {
            progress(0, 1);
            foreach (var item in new[] { selection.Instrument, selection.Snapshot }.OfType<KontaktLibrary.Item>())
            { Workspace.VerifyParents(Path.GetDirectoryName(item.Path)!); Workspace.DemandRegular(item.Path); }
            string request = Path.Combine(sandbox, "prepare.json"), output = Path.Combine(sandbox, "prepared.vstpreset");
            await File.WriteAllTextAsync(request, JsonSerializer.Serialize(new { version = 1, instrument = selection.Instrument.Path,
                snapshot = selection.Snapshot?.Path, nonce = Guid.NewGuid().ToString("N") }), token);
            var result = await WorkerProcess.Run(worker!, ["kontakt-prepare", "--plugin", target.Path, "--class-id", target.ClassId,
                "--script", request, "--workspace", sandbox, "--output", output, "--max-seconds", "120", "--allow-write",
                "--dialog-wait-seconds", dialogWaitSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture)],
                sandbox, TimeSpan.FromSeconds(125), token, 16384);
            if (result.GetProperty("schema_version").GetInt32() != 1 || result.GetProperty("adapter").GetString() != "kontakt_lua_editor" ||
                result.GetProperty("class_id").GetString() != target.ClassId || result.GetProperty("instrument_count").GetInt32() != 1 ||
                result.GetProperty("dialog_wait_seconds").GetInt32() != dialogWaitSeconds)
                throw new McpFault("kontakt_prepare_invalid_output");
            Workspace.VerifyParents(sandbox);
            byte[] state = Workspace.ReadBounded(output, SoundShelf.MaximumStateBytes);
            SoundShelf.ValidateState(state, target.ClassId);
            Workspace.AtomicWrite(pending, state, overwrite: false);
            token.ThrowIfCancellationRequested();
            workspace.VstAudit("kontakt_prepare_begin", "completed", 1);
            var artifact = workspace.PublishBatch([(pending, ".vstpreset", "application/x-vstpreset")], revision, token)[0];
            progress(1, 1);
            return JsonSerializer.SerializeToElement(new { artifact, catalog_id = target.Id, plugin_version = target.Version,
                library_id = selection.LibraryId, instrument_id = selection.Instrument.Id, snapshot_id = selection.Snapshot?.Id,
                state_captured = true, audible_verified = false, project_changed = false,
                dialog_wait_seconds = dialogWaitSeconds,
                next_step = "instrument_state_set_then_render_and_sound_register" });
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error)
        {
            string stage = "unknown";
            try {
                string value = System.Text.Encoding.ASCII.GetString(Workspace.ReadBounded(Path.Combine(sandbox, "prepare.progress"), 64));
                if (value is "editor_open" or "dialog_owned" or "dialog_common" or "filename_control" or "file_submitted" or "lua_receipt" or "state_captured" or
                    "dialog_filename_missing" or "dialog_type_missing" or "dialog_browser_missing" or "dialog_buttons_missing" or
                    "shortcut_vst_sent" or "shortcut_native_target" or "shortcut_native_posted") stage = value;
            } catch { }
            string code = error is WorkerFailure workerFailure ? workerFailure.Code : error is McpFault fault ? fault.Code : "kontakt_prepare_failed";
            workspace.VstAudit("kontakt_prepare_begin", code, 1);
            throw new WorkerFailure(code, JsonSerializer.SerializeToElement(new { code, preparation_stage = stage, dialog_wait_seconds = dialogWaitSeconds,
                rollback = new { attempted = false, restored = (bool?)null, status = "not_attempted" } }));
        }
        finally { File.Delete(pending); Directory.Delete(sandbox, recursive: true); }
    }
}
