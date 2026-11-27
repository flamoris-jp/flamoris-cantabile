using System.Text;
using System.Text.Json;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

public sealed class BandRenderer(Workspace workspace, VstCatalog catalog)
{
    private readonly string? worker = Environment.GetEnvironmentVariable("CANTABILE_RENDERER");
    internal sealed record Part(string TrackId, VstCatalog.Entry Instrument, byte[]? State, byte[] Midi, double Volume, double Pan, int? WarmupMs = null, bool? Realtime = null, int PrerollMs = 0);
    internal Part[] Prepare(JsonElement plan)
    {
        if (!File.Exists(worker)) throw new McpFault("vst_backend_unavailable");
        var parts = plan.GetProperty("parts").EnumerateArray().Select(p => {
            string track = p.GetProperty("track_id").GetString()!;
            var instrument = p.GetProperty("instrument");
            if (!instrument.TryGetProperty("catalog_id", out var id)) throw TrackFailure("instrument_unassigned", track);
            VstCatalog.Entry entry;
            try { entry = catalog.Resolve(id.GetString()!); }
            catch (McpFault failure) { throw TrackFailure(failure.Code, track); }
            if (instrument.TryGetProperty("plugin_version", out var version) && version.GetString() != entry.Version) throw TrackFailure("sound_version_mismatch", track);
            byte[]? state = State(instrument);
            if (state is not null) { try { SoundShelf.ValidateState(state, entry.ClassId); } catch (McpFault failure) { throw TrackFailure(failure.Code, track); } }
            return new Part(track, entry, state, Convert.FromBase64String(p.GetProperty("midi_base64").GetString()!),
                p.GetProperty("volume").GetDouble(), p.GetProperty("pan").GetDouble(),
                instrument.TryGetProperty("warmup_ms", out var warmup) ? warmup.GetInt32() : null,
                instrument.TryGetProperty("realtime", out var realtime) ? realtime.GetBoolean() : null,
                instrument.TryGetProperty("preroll_ms", out var preroll) ? preroll.GetInt32() : 0);
        }).ToArray();
        if (parts.Length is < 1 or > 32) throw new McpFault("render_track_limit");
        return parts;
    }
    internal static byte[]? State(JsonElement instrument) => instrument.TryGetProperty("state_base64", out var s) ? Convert.FromBase64String(s.GetString()!) : null;
    private static WorkerFailure TrackFailure(string code, string track, WorkerFailure? source = null)
    {
        var summary = JsonSerializer.SerializeToNode(new { code, track_id = track,
            rollback = new { attempted = (bool?)null, restored = (bool?)null, status = "unknown" } })!.AsObject();
        // Keep host-created overflow counters, never vendor output or paths.
        if (source is { Code: "vst_output_limit" or "vst_worker_crashed" })
            foreach (string name in new[] { "output_limit", "termination", "result_channel" })
                if (source.Summary.TryGetProperty(name, out var value))
                    summary[name] = System.Text.Json.Nodes.JsonNode.Parse(value.GetRawText());
        return new(code, JsonSerializer.SerializeToElement(summary));
    }
    internal async Task<JsonElement> Run(Part[] parts, long revision, int maximumSeconds, Action<int, int> progress, CancellationToken token)
    {
        string sandbox = workspace.CreateVstSandbox(); var pending = new List<string>();
        try
        {
            int warmup = int.TryParse(Environment.GetEnvironmentVariable("CANTABILE_VST3_WARMUP_MS"), out int w) && w is >= 0 and <= 30000 ? w : 0;
            bool realtime = Environment.GetEnvironmentVariable("CANTABILE_VST3_REALTIME")?.ToLowerInvariant() is "true" or "1";
            var sources = new List<string>(); progress(0, parts.Length + 1);
            for (int i = 0; i < parts.Length; ++i)
            {
                token.ThrowIfCancellationRequested(); var part = parts[i];
                string midi = Path.Combine(sandbox, $"{i}.mid"), output = Path.Combine(sandbox, $"{i}.wav");
                await File.WriteAllBytesAsync(midi, part.Midi, token);
                var arguments = new List<string> { "--plugin", part.Instrument.Path, "--class-id", part.Instrument.ClassId,
                    "--midi", midi, "--output", output, "--float-output", "--max-seconds", maximumSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture) };
                if (part.State is not null)
                {
                    string state = Path.Combine(sandbox, $"{i}.vstpreset"); await File.WriteAllBytesAsync(state, part.State, token);
                    arguments.AddRange(["--preset", state]);
                }
                int partWarmup = part.WarmupMs ?? warmup;
                if (part.Realtime ?? realtime) arguments.Add("--realtime");
                arguments.AddRange(["--warmup-ms", partWarmup.ToString(System.Globalization.CultureInfo.InvariantCulture)]);
                arguments.AddRange(["--preroll-ms", part.PrerollMs.ToString(System.Globalization.CultureInfo.InvariantCulture)]);
                try
                {
                    int stderrMaximum = part.Instrument.Vendor == "Native Instruments" &&
                        (part.Instrument.Name is "Kontakt 7" or "Kontakt 8") ? 1024 * 1024 : 16384;
                    var result = await WorkerProcess.Run(worker!, arguments, sandbox,
                        TimeSpan.FromSeconds(maximumSeconds + 30) + TimeSpan.FromMilliseconds(partWarmup + part.PrerollMs),
                        token, stderrMaximum: stderrMaximum);
                    if (result.GetProperty("backend").GetString() != "vst3" || result.GetProperty("sample_format").GetString() != "ieee_float32le") throw new McpFault("invalid_audio");
                    Workspace.DemandRegular(output);
                    long size = new FileInfo(output).Length;
                    if (size > Workspace.MaximumArtifactBytes || (size - 44) % 8 != 0 || size < 52) throw new McpFault("artifact_too_large");
                    // Raw float sources, processed float stems, final PCM mix.
                    if ((size - 44) * (2L * parts.Length + 1) + 44L * (parts.Length + 1) > Workspace.MaximumTotalArtifactBytes) throw new McpFault("workspace_quota");
                }
                catch (OperationCanceledException) { throw; }
                catch (Exception e) { throw TrackFailure(e is WorkerFailure f ? f.Code : e is McpFault m ? m.Code : "render_failed", part.TrackId, e as WorkerFailure); }
                sources.Add(output); progress(i + 1, parts.Length + 1);
            }
            foreach (var _ in parts) pending.Add(workspace.TempFile(".wav"));
            pending.Add(workspace.TempFile(".wav"));
            var measurements = Mix(sources, pending, parts, token);
            foreach (var part in parts) catalog.RecordAudible(part.Instrument.Id);
            token.ThrowIfCancellationRequested();
            var artifacts = workspace.PublishBatch(pending, revision, token);
            progress(parts.Length + 1, parts.Length + 1);
            return JsonSerializer.SerializeToElement(new { artifact = artifacts[^1], audio = measurements,
                stems = parts.Select((p, i) => new { track_id = p.TrackId, catalog_id = p.Instrument.Id, artifact = artifacts[i], sample_format = "ieee_float32le" }),
                timeline = "song_origin", instance_scope = "independent_worker_per_track" });
        }
        finally
        {
            foreach (var file in pending) File.Delete(file);
            Directory.Delete(sandbox, recursive: true);
        }
    }
    private static object Mix(List<string> sources, List<string> outputs, Part[] parts, CancellationToken token)
    {
        var readers = new List<BinaryReader>(); var writers = new List<BinaryWriter>();
        try
        {
            long frames = -1;
            foreach (string file in sources)
            {
                Workspace.DemandRegular(file); var reader = new BinaryReader(File.OpenRead(file)); readers.Add(reader);
                byte[] header = reader.ReadBytes(44);
                if (header.Length != 44 || Encoding.ASCII.GetString(header, 0, 4) != "RIFF" || Encoding.ASCII.GetString(header, 8, 8) != "WAVEfmt " ||
                    BitConverter.ToUInt32(header, 16) != 16 || BitConverter.ToUInt16(header, 20) != 3 || BitConverter.ToUInt16(header, 22) != 2 ||
                    BitConverter.ToUInt32(header, 24) != 48000 || BitConverter.ToUInt16(header, 32) != 8 || BitConverter.ToUInt16(header, 34) != 32 ||
                    Encoding.ASCII.GetString(header, 36, 4) != "data" || BitConverter.ToUInt32(header, 40) != reader.BaseStream.Length - 44) throw new McpFault("invalid_audio");
                long count = (reader.BaseStream.Length - 44) / 8;
                if (frames >= 0 && count != frames) throw new McpFault("stem_alignment_failed"); frames = count;
            }
            for (int i = 0; i < outputs.Count; ++i)
            {
                var writer = new BinaryWriter(new FileStream(outputs[i], FileMode.CreateNew, FileAccess.Write, FileShare.None)); writers.Add(writer);
                if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(outputs[i], UnixFileMode.UserRead | UnixFileMode.UserWrite);
                WriteHeader(writer, checked((int)frames), i != outputs.Count - 1);
            }
            double peak = 0; long clipped = 0;
            // Streaming keeps memory independent of song duration; the internal
            // sum is double precision and saturation happens only at PCM export.
            for (long frame = 0; frame < frames; ++frame)
            {
                if (frame % 4096 == 0) token.ThrowIfCancellationRequested();
                for (int channel = 0; channel < 2; ++channel)
                {
                    double sum = 0;
                    for (int i = 0; i < readers.Count; ++i)
                    {
                        double sample = readers[i].ReadSingle();
                        if (!double.IsFinite(sample)) throw new McpFault("invalid_audio");
                        double balance = channel == 0 ? Math.Min(1, 1 - parts[i].Pan) : Math.Min(1, 1 + parts[i].Pan);
                        sample *= parts[i].Volume * balance; sum += sample;
                        if (!float.IsFinite((float)sample)) throw new McpFault("invalid_audio");
                        writers[i].Write((float)sample);
                    }
                    if (!double.IsFinite(sum)) throw new McpFault("invalid_audio");
                    peak = Math.Max(peak, Math.Abs(sum)); if (Math.Abs(sum) > 1) ++clipped;
                    writers[^1].Write((short)Math.Round(Math.Clamp(sum, -1, 1) * 32767, MidpointRounding.ToEven));
                }
            }
            return new { backend = "vst3", sample_rate = 48000, channels = 2, frames, duration_seconds = frames / 48000.0, peak,
                clipped_samples = clipped, sample_format = "pcm_s16le", internal_mix = "float64", pan_law = "stereo_balance_center_unity" };
        }
        finally { foreach (var r in readers) r.Dispose(); foreach (var w in writers) w.Dispose(); }
    }
    private static void WriteHeader(BinaryWriter writer, int frames, bool floating)
    {
        int alignment = floating ? 8 : 4, size = checked(frames * alignment);
        writer.Write(Encoding.ASCII.GetBytes("RIFF")); writer.Write(size + 36); writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
        writer.Write(16); writer.Write((short)(floating ? 3 : 1)); writer.Write((short)2); writer.Write(48000); writer.Write(48000 * alignment);
        writer.Write((short)alignment); writer.Write((short)(floating ? 32 : 16)); writer.Write(Encoding.ASCII.GetBytes("data")); writer.Write(size);
    }
}
