using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

// Product-specific processing, supervised by the existing job/worker/artifact owners.
internal sealed class VstCapture(Workspace workspace)
{
    private readonly string? worker = Environment.GetEnvironmentVariable("CANTABILE_RENDERER");
    internal async Task<JsonElement> Run(BandRenderer.Part part, long revision, int start, int? end, int maximumEvents,
        int maximumSeconds, Action<int, int> progress, CancellationToken token)
    {
        if (!File.Exists(worker)) throw new McpFault("vst_backend_unavailable");
        string sandbox = workspace.CreateVstSandbox();
        var pending = new List<(string Path, string Extension, string Mime)>();
        try
        {
            progress(0, 1);
            string input = Path.Combine(sandbox, "input.mid"), output = Path.Combine(sandbox, "generated.mid");
            await File.WriteAllBytesAsync(input, part.Midi, token);
            int warmup = part.WarmupMs ?? (int.TryParse(Environment.GetEnvironmentVariable("CANTABILE_VST3_WARMUP_MS"), out int w) && w is >= 0 and <= 30000 ? w : 0);
            bool realtime = part.Realtime ?? (Environment.GetEnvironmentVariable("CANTABILE_VST3_REALTIME")?.ToLowerInvariant() is "true" or "1");
            var arguments = new List<string> { "capture", "--plugin", part.Instrument.Path, "--class-id", part.Instrument.ClassId,
                "--midi", input, "--output", output, "--start-tick", start.ToString(CultureInfo.InvariantCulture),
                "--max-events", maximumEvents.ToString(CultureInfo.InvariantCulture), "--max-seconds", maximumSeconds.ToString(CultureInfo.InvariantCulture),
                "--warmup-ms", warmup.ToString(CultureInfo.InvariantCulture), "--preroll-ms", part.PrerollMs.ToString(CultureInfo.InvariantCulture) };
            if (end is { } stop) arguments.AddRange(["--end-tick", stop.ToString(CultureInfo.InvariantCulture)]);
            if (realtime) arguments.Add("--realtime");
            if (part.State is { } state)
            {
                string preset = Path.Combine(sandbox, "state.vstpreset");
                await File.WriteAllBytesAsync(preset, state, token); arguments.AddRange(["--preset", preset]);
            }
            var response = await WorkerProcess.Run(worker!, arguments, sandbox,
                TimeSpan.FromSeconds(maximumSeconds + 30) + TimeSpan.FromMilliseconds(warmup + part.PrerollMs), token);
            var report = Validate(response, maximumEvents);
            byte[] midi = Workspace.ReadBounded(output, Workspace.MaximumMidiBytes);
            report["provenance"] = JsonSerializer.SerializeToNode(new { kind = "vst3_capture", version = 1,
                source_track_id = part.TrackId, catalog_id = part.Instrument.Id, class_id = part.Instrument.ClassId,
                plugin_version = part.Instrument.Version, source_revision = revision,
                midi_sha256 = Workspace.Digest(midi), input_sha256 = Workspace.Digest(part.Midi),
                state_sha256 = Workspace.Digest(part.State ?? []), start_tick = start, end_tick = report["end_tick"]!.GetValue<int>() });
            report["state_source"] = part.State is null ? "plugin_default" : "saved_standard_state";
            byte[] metadata = JsonSerializer.SerializeToUtf8Bytes(report);
            if (metadata.Length > 2 * 1024 * 1024) throw new McpFault("capture_metadata_limit");
            pending.Add((workspace.TempFile(".mid"), ".mid", "audio/midi"));
            pending.Add((workspace.TempFile(".vstcapture"), ".vstcapture", "application/vnd.flamoris.vstcapture+json"));
            Workspace.AtomicWrite(pending[0].Path, midi, overwrite: false);
            Workspace.AtomicWrite(pending[1].Path, metadata, overwrite: false);
            token.ThrowIfCancellationRequested();
            var artifacts = workspace.PublishBatch(pending, revision, token);
            progress(1, 1);
            return JsonSerializer.SerializeToElement(new { capture_status = report["capture_status"]!.GetValue<string>(),
                event_count = report["event_count"]!.GetValue<int>(), source_track_id = part.TrackId,
                midi = artifacts[0], report = artifacts[1], automatic_reinjection = false });
        }
        finally
        {
            foreach (var item in pending) File.Delete(item.Path);
            Directory.Delete(sandbox, recursive: true);
        }
    }
    private static JsonObject Validate(JsonElement response, int maximumEvents)
    {
        // Do not forward arbitrary worker/plugin strings or unknown properties.
        if (response.GetProperty("schema_version").GetInt32() != 1 || response.GetProperty("backend").GetString() != "vst3" ||
            response.GetProperty("capture_supported").ValueKind != JsonValueKind.True || response.GetProperty("bus_count").GetInt32() != 1)
            throw new McpFault("capture_invalid_output");
        string status = response.GetProperty("capture_status").GetString()!;
        if (status is not ("captured" or "no_events")) throw new McpFault("capture_invalid_output");
        var result = new JsonObject { ["schema_version"] = 1, ["capture_status"] = status };
        foreach (string field in new[] { "ppq", "start_tick", "end_tick", "sample_rate", "event_count", "total_events", "discarded_preroll",
            "discarded_before_range", "discarded_tail", "unmatched_note_offs", "unterminated_notes", "overlapping_note_ons" })
        {
            long value = response.GetProperty(field).GetInt64();
            if (value < 0 || value > int.MaxValue) throw new McpFault("capture_invalid_output");
            result[field] = (int)value;
        }
        var records = response.GetProperty("events");
        if (records.GetArrayLength() > maximumEvents || records.GetArrayLength() != result["event_count"]!.GetValue<int>() ||
            (status == "captured") != (records.GetArrayLength() > 0)) throw new McpFault("capture_invalid_output");
        var events = new JsonArray();
        foreach (var record in records.EnumerateArray())
        {
            var item = new JsonObject();
            foreach (string field in new[] { "sample_frame", "tick", "order", "bus_index", "type", "channel", "pitch", "note_id", "flags" })
                item[field] = record.GetProperty(field).GetInt64();
            double value = record.GetProperty("value").GetDouble();
            if (!double.IsFinite(value) || value is < 0 or > 1) throw new McpFault("capture_invalid_output");
            item["value"] = value;
            string? phase = record.GetProperty("phase").GetString();
            if (phase is not ("capture" or "postroll_note_off")) throw new McpFault("capture_invalid_output");
            item["phase"] = phase;
            events.Add(item);
        }
        result["events"] = events;
        result["conversion"] = new JsonObject { ["ticks"] = "nearest_integer_half_up_from_sample_offsets",
            ["velocity_pressure"] = "nearest_7bit_half_up_note_on_minimum_1", ["note_ids"] = "diagnostics_only_smf_has_no_note_identity",
            ["overlap_endings"] = "raw_messages_preserved_no_pairing_or_synthetic_note_offs",
            ["latency"] = "project_event_time_without_audio_latency_shift", ["nonzero_tuning"] = "rejected" };
        return result;
    }
    internal JsonElement Read(string midiId, string reportId)
    {
        byte[] midi = workspace.VstInputArtifact(midiId, ".mid"), report = workspace.VstInputArtifact(reportId, ".vstcapture");
        using var parsed = JsonDocument.Parse(report, new() { MaxDepth = 16 }); var value = parsed.RootElement;
        if (value.GetProperty("schema_version").GetInt32() != 1 || value.GetProperty("provenance").GetProperty("midi_sha256").GetString() != Workspace.Digest(midi))
            throw new McpFault("capture_artifact_mismatch");
        return value.Clone();
    }
    internal JsonElement Preview(string midiId, string reportId)
    {
        var report = Read(midiId, reportId); var summary = JsonNode.Parse(report.GetRawText())!.AsObject();
        summary["events"] = JsonSerializer.SerializeToNode(report.GetProperty("events").EnumerateArray().Take(64).ToArray());
        summary["preview_limit"] = 64; summary["automatic_reinjection"] = false;
        return JsonSerializer.SerializeToElement(summary);
    }
}
