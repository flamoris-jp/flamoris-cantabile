using System.Buffers.Binary;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

/// <summary>Private, durable sound snapshots. Never an alternate project editor.</summary>
public sealed class SoundShelf(Workspace workspace, VstCatalog catalog)
{
    public const int MaximumStateBytes = 1024 * 1024;
    private const int MaximumRecordBytes = 1500000, MaximumSounds = 128;
    private const long MaximumTotalStateBytes = 64L * 1024 * 1024;
    private readonly object gate = new();
    public sealed record Verification(bool InitialAudible, bool RestoredAudible, bool HumanReviewed, string Evidence, string? CheckedAt);
    public sealed record Entry(string SoundId, string DisplayName, string Category, string[] Roles, string[] Tags,
        string ClassificationSource, string CatalogId, string ClassId, string PluginVersion, string StateBase64,
        string StateSha256, int StateSize, int WarmupMs, bool Realtime, string CreatedAt, Verification Verification, int PrerollMs = 0, JsonElement? Performance = null);
    private string FilePath(string id)
    {
        if (!id.StartsWith("sound-", StringComparison.Ordinal) || !Guid.TryParseExact(id[6..], "N", out _)) throw new McpFault("invalid_sound_id");
        string path = Path.Combine(workspace.SoundDirectory(), id + ".json"); Workspace.DemandRegular(path); return path;
    }
    private Entry Read(string id, bool checkState = true)
    {
        string path = FilePath(id);
        if (!File.Exists(path)) throw new McpFault("sound_missing");
        try
        {
            var entry = JsonSerializer.Deserialize<Entry>(Workspace.ReadBounded(path, MaximumRecordBytes)) ?? throw new JsonException();
            if (entry.SoundId != id || entry.StateSize is < 48 or > MaximumStateBytes || entry.WarmupMs is < 0 or > 30000 || entry.PrerollMs is < 0 or > 30000 ||
                entry.ClassId.Length != 32 || !entry.ClassId.All(char.IsAsciiHexDigit) || entry.StateSha256.Length != 64 ||
                entry.Roles.Length > 16 || entry.Tags.Length > 16 || entry.Verification is null) throw new JsonException();
            if (checkState) _ = State(entry);
            if (entry.Performance is { } performance) {
                ValidatePerformance(performance);
                if (performance.TryGetProperty("capture_evidence", out var evidence) &&
                    (evidence.GetProperty("state_sha256").GetString() != entry.StateSha256 || evidence.GetProperty("class_id").GetString() != entry.ClassId ||
                        evidence.GetProperty("plugin_version").GetString() != entry.PluginVersion)) throw new JsonException();
            }
            return entry;
        }
        catch (Exception e) when (e is JsonException or FormatException or NullReferenceException or ArgumentException)
        { throw new McpFault("sound_state_corrupt"); }
    }
    private void Write(Entry entry, bool overwrite)
    {
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(entry);
        if (bytes.Length > MaximumRecordBytes) throw new McpFault("sound_limit");
        Workspace.AtomicWrite(FilePath(entry.SoundId), bytes, overwrite);
    }
    private string[] Ids()
    {
        string directory = workspace.SoundDirectory();
        var files = Directory.EnumerateFiles(directory, "sound-*.json").Take(MaximumSounds + 1).ToArray();
        if (files.Length > MaximumSounds) throw new McpFault("sound_limit");
        return files.Select(Path.GetFileNameWithoutExtension).Select(x => x!).Order(StringComparer.Ordinal).ToArray();
    }
    // Validate the standard container before retaining it. Plugin-level state
    // semantics/sample availability still require an actual worker render.
    internal static void ValidateState(byte[] bytes, string classId)
    {
        if (bytes.Length is < 48 or > MaximumStateBytes || Encoding.ASCII.GetString(bytes, 0, 4) != "VST3" ||
            BinaryPrimitives.ReadInt32LittleEndian(bytes.AsSpan(4, 4)) != 1 ||
            !Encoding.ASCII.GetString(bytes, 8, 32).Equals(classId, StringComparison.OrdinalIgnoreCase)) throw new McpFault("invalid_instrument_state");
        long offset = BinaryPrimitives.ReadInt64LittleEndian(bytes.AsSpan(40, 8));
        if (offset < 48 || offset > bytes.Length - 8) throw new McpFault("invalid_instrument_state");
        int start = (int)offset;
        if (Encoding.ASCII.GetString(bytes, start, 4) != "List") throw new McpFault("invalid_instrument_state");
        int count = BinaryPrimitives.ReadInt32LittleEndian(bytes.AsSpan(start + 4, 4));
        if (count is < 1 or > 128 || count * 20L > bytes.Length - offset - 8) throw new McpFault("invalid_instrument_state");
        bool component = false; var ranges = new List<(long Start, long End)>(); var ids = new HashSet<string>();
        for (int i = 0; i < count; ++i)
        {
            int item = start + 8 + i * 20; string id = Encoding.ASCII.GetString(bytes, item, 4);
            long position = BinaryPrimitives.ReadInt64LittleEndian(bytes.AsSpan(item + 4, 8));
            long size = BinaryPrimitives.ReadInt64LittleEndian(bytes.AsSpan(item + 12, 8));
            if (!ids.Add(id) || position < 48 || size < 0 || position > offset || size > offset - position ||
                ranges.Any(r => position < r.End && position + size > r.Start)) throw new McpFault("invalid_instrument_state");
            ranges.Add((position, position + size)); component |= id == "Comp" && size > 0;
        }
        if (!component) throw new McpFault("invalid_instrument_state");
    }
    internal static byte[] State(Entry entry)
    {
        byte[] bytes = Convert.FromBase64String(entry.StateBase64);
        if (bytes.Length != entry.StateSize || Workspace.Digest(bytes) != entry.StateSha256) throw new McpFault("sound_state_corrupt");
        ValidateState(bytes, entry.ClassId); return bytes;
    }
    private VstCatalog.Entry Installed(Entry entry)
    {
        var installed = catalog.Resolve(entry.CatalogId);
        if (!installed.ClassId.Equals(entry.ClassId, StringComparison.OrdinalIgnoreCase)) throw new McpFault("sound_class_mismatch");
        if (installed.Version != entry.PluginVersion) throw new McpFault("sound_version_mismatch");
        return installed;
    }
    internal (Entry Sound, VstCatalog.Entry Instrument, byte[] State) Resolve(string id)
    {
        lock (gate) { var sound = Read(id); return (sound, Installed(sound), State(sound)); }
    }
    private object View(Entry entry)
    {
        string? error = null;
        try { _ = Installed(entry); } catch (McpFault failure) { error = failure.Code; }
        return new { sound_id = entry.SoundId, display_name = entry.DisplayName, category = entry.Category, roles = entry.Roles,
            tags = entry.Tags, classification_source = entry.ClassificationSource, catalog_id = entry.CatalogId,
            class_id = entry.ClassId, plugin_version = entry.PluginVersion, performance = entry.Performance,
            performance_evidence_source = "operator_declaration_with_optional_host_observed_capture",
            state = new { reference = entry.SoundId, sha256 = entry.StateSha256, size = entry.StateSize, captured = true },
            loading = new { warmup_ms = entry.WarmupMs, realtime = entry.Realtime, preroll_ms = entry.PrerollMs }, created_at = entry.CreatedAt,
            verification = new { initial_audible = entry.Verification.InitialAudible, restored_audible = entry.Verification.RestoredAudible,
                human_reviewed = entry.Verification.HumanReviewed, evidence = entry.Verification.Evidence, checked_at = entry.Verification.CheckedAt,
                source = "operator_attestation" }, available = error is null, availability_error = error,
            restoration = entry.Verification.RestoredAudible ? "operator_verified" : "unverified" };
    }
    private static void Closed(JsonElement value, params string[] allowed)
    {
        if (value.ValueKind != JsonValueKind.Object) throw new ArgumentException();
        var seen = new HashSet<string>();
        foreach (var p in value.EnumerateObject()) if (!allowed.Contains(p.Name) || !seen.Add(p.Name)) throw new ArgumentException();
    }
    private static string Text(JsonElement input, string name, int maximum = 128)
    {
        string? value = input.GetProperty(name).GetString();
        if (string.IsNullOrWhiteSpace(value) || value.Length > maximum || value.Any(char.IsControl)) throw new ArgumentException();
        return value;
    }
    private static string[] Labels(JsonElement input, string name)
    {
        if (!input.TryGetProperty(name, out var list)) return [];
        if (list.ValueKind != JsonValueKind.Array || list.GetArrayLength() > 16) throw new ArgumentException();
        return list.EnumerateArray().Select(x => x.GetString()).Select(x =>
            !string.IsNullOrWhiteSpace(x) && x.Length <= 64 && !x.Any(char.IsControl) ? x : throw new ArgumentException()).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
    }
    public static JsonElement RegisterInput(JsonElement input)
    {
        Closed(input, "track_id", "file_id", "display_name", "category", "roles", "tags", "classification_source", "warmup_ms", "realtime", "preroll_ms");
        _ = Text(input, "track_id"); _ = Text(input, "display_name"); _ = Text(input, "category", 64);
        _ = Text(input, "classification_source", 512); _ = Labels(input, "roles"); _ = Labels(input, "tags");
        if (input.TryGetProperty("file_id", out _)) _ = Text(input, "file_id", 32);
        if (input.TryGetProperty("warmup_ms", out var warmup) && warmup.GetInt32() is < 0 or > 30000) throw new ArgumentException();
        if (input.TryGetProperty("preroll_ms", out var preroll) && preroll.GetInt32() is < 0 or > 30000) throw new ArgumentException();
        if (input.TryGetProperty("realtime", out var realtime)) _ = realtime.GetBoolean();
        return input.Clone();
    }
    public JsonElement Register(JsonElement input, VstCatalog.Entry instrument, byte[] state)
    {
        ValidateState(state, instrument.ClassId);
        lock (gate)
        {
            var ids = Ids(); if (ids.Length >= MaximumSounds) throw new McpFault("sound_limit");
            if (ids.Sum(id => (long)Read(id).StateSize) + state.Length > MaximumTotalStateBytes) throw new McpFault("sound_quota");
            var entry = new Entry("sound-" + Guid.NewGuid().ToString("N"), Text(input, "display_name"), Text(input, "category", 64),
                Labels(input, "roles"), Labels(input, "tags"), Text(input, "classification_source", 512), instrument.Id, instrument.ClassId,
                instrument.Version, Convert.ToBase64String(state), Workspace.Digest(state), state.Length,
                input.TryGetProperty("warmup_ms", out var warmup) ? warmup.GetInt32() : 0,
                input.TryGetProperty("realtime", out var realtime) && realtime.GetBoolean(), DateTime.UtcNow.ToString("O"), new(false, false, false, "", null),
                input.TryGetProperty("preroll_ms", out var preroll) ? preroll.GetInt32() : 0);
            Write(entry, false); return JsonSerializer.SerializeToElement(View(entry));
        }
    }
    public static JsonElement SearchInput(JsonElement input)
    {
        Closed(input, "category", "role", "tag", "control_kind", "verification", "offset", "limit");
        if (input.TryGetProperty("control_kind", out _) && Text(input, "control_kind") is not ("trigger" or "keyswitch" or "pattern" or "cc" or "parameter" or "gui")) throw new ArgumentException();
        foreach (string field in new[] { "category", "role", "tag" }) if (input.TryGetProperty(field, out _)) _ = Text(input, field, 64);
        if (input.TryGetProperty("verification", out _) && Text(input, "verification") is not ("initial_audible" or "state_captured" or "restored_audible" or "human_reviewed" or "unverified" or "capture_observed")) throw new ArgumentException();
        if (input.TryGetProperty("offset", out var offset) && offset.GetInt32() is < 0 or > MaximumSounds) throw new ArgumentException();
        if (input.TryGetProperty("limit", out var limit) && limit.GetInt32() is < 1 or > 32) throw new ArgumentException();
        return input.Clone();
    }
    public JsonElement Search(JsonElement input)
    {
        lock (gate)
        {
            var matches = new List<object>();
            foreach (string id in Ids())
            {
                Entry entry;
                try { entry = Read(id); }
                catch (McpFault failure) { matches.Add(new { sound_id = id, available = false, availability_error = failure.Code }); continue; }
                if (input.TryGetProperty("category", out var category) && !entry.Category.Equals(category.GetString(), StringComparison.OrdinalIgnoreCase) ||
                    input.TryGetProperty("role", out var role) && !entry.Roles.Contains(role.GetString(), StringComparer.OrdinalIgnoreCase) ||
                    input.TryGetProperty("tag", out var tag) && !entry.Tags.Contains(tag.GetString(), StringComparer.OrdinalIgnoreCase)) continue;
                if (input.TryGetProperty("verification", out var verification) && !(verification.GetString() switch {
                    "initial_audible" => entry.Verification.InitialAudible, "state_captured" => true,
                    "restored_audible" => entry.Verification.RestoredAudible, "human_reviewed" => entry.Verification.HumanReviewed,
                    "capture_observed" => entry.Performance is { } observed && observed.TryGetProperty("capture_evidence", out _),
                    _ => !entry.Verification.RestoredAudible })) continue;
                if (input.TryGetProperty("control_kind", out var controlKind) && (entry.Performance is not { } performance ||
                    !performance.GetProperty("controls").EnumerateArray().Any(c => c.GetProperty("kind").GetString() == controlKind.GetString()))) continue;
                matches.Add(View(entry));
            }
            int offset = input.TryGetProperty("offset", out var o) ? o.GetInt32() : 0, limit = input.TryGetProperty("limit", out var l) ? l.GetInt32() : 16;
            return JsonSerializer.SerializeToElement(new { sounds = matches.Skip(offset).Take(limit), total = matches.Count,
                next_offset = offset + limit < matches.Count ? (int?)(offset + limit) : null,
                limits = new { maximum_sounds = MaximumSounds, maximum_state_bytes = MaximumStateBytes, maximum_total_state_bytes = MaximumTotalStateBytes } });
        }
    }
    public static JsonElement VerifyInput(JsonElement input)
    {
        Closed(input, "sound_id", "initial_audible", "restored_audible", "human_reviewed", "evidence");
        _ = Text(input, "sound_id"); _ = Text(input, "evidence", 1024);
        foreach (string field in new[] { "initial_audible", "restored_audible", "human_reviewed" }) _ = input.GetProperty(field).GetBoolean();
        return input.Clone();
    }
    public JsonElement Verify(JsonElement input)
    {
        lock (gate)
        {
            var entry = Read(Text(input, "sound_id"));
            entry = entry with { Verification = new(input.GetProperty("initial_audible").GetBoolean(), input.GetProperty("restored_audible").GetBoolean(),
                input.GetProperty("human_reviewed").GetBoolean(), Text(input, "evidence", 1024), DateTime.UtcNow.ToString("O")) };
            Write(entry, true); return JsonSerializer.SerializeToElement(View(entry));
        }
    }
    public JsonElement Remove(string id, string expectedSha256)
    {
        lock (gate)
        {
            var entry = Read(id, checkState: false); if (entry.StateSha256 != expectedSha256) throw new McpFault("sound_changed");
            File.Delete(FilePath(id)); return JsonSerializer.SerializeToElement(new { sound_id = id, removed = true });
        }
    }
    internal static void ValidatePerformance(JsonElement value)
    {
        if (System.Text.Encoding.UTF8.GetByteCount(value.GetRawText()) > 16384) throw new ArgumentException();
        using var native = new NativeEngine(); native.Call(new { op = "performance.validate", data = value });
    }
    internal JsonElement SetPerformance(string id, string expected, JsonElement performance, JsonElement? captureReport = null)
    {
        ValidatePerformance(performance);
        lock (gate)
        {
            var entry = Read(id); if (entry.StateSha256 != expected) throw new McpFault("sound_changed");
            if (performance.TryGetProperty("capture_evidence", out _)) throw new ArgumentException("Capture evidence is host-owned.");
            if (captureReport is { } report) {
                var p = report.GetProperty("provenance");
                if (report.GetProperty("capture_status").GetString() != "captured" || p.GetProperty("state_sha256").GetString() != entry.StateSha256 ||
                    p.GetProperty("class_id").GetString() != entry.ClassId || p.GetProperty("plugin_version").GetString() != entry.PluginVersion)
                    throw new McpFault("sound_capture_mismatch");
                var metadata = JsonNode.Parse(performance.GetRawText())!.AsObject(); var evidence = new JsonObject();
                foreach (string f in new[] { "state_sha256", "input_sha256", "midi_sha256", "class_id", "plugin_version", "source_revision" })
                    evidence[f] = JsonNode.Parse(p.GetProperty(f).GetRawText());
                evidence["event_count"] = report.GetProperty("event_count").GetInt32(); metadata["capture_evidence"] = evidence;
                performance = JsonSerializer.SerializeToElement(metadata); ValidatePerformance(performance);
            }
            entry = entry with { Performance = performance.Clone() }; Write(entry, true);
            return JsonSerializer.SerializeToElement(View(entry));
        }
    }
    internal static object Instrument(Entry entry) => new { backend = "vst3", catalog_id = entry.CatalogId, state_base64 = entry.StateBase64,
        sound_id = entry.SoundId, plugin_version = entry.PluginVersion, warmup_ms = entry.WarmupMs, realtime = entry.Realtime, preroll_ms = entry.PrerollMs, performance = entry.Performance };

    // Original, fixed test notes. No bundled/vendor phrase collections.
    internal static byte[] PreviewMidi(string phrase)
    {
        var events = new List<object>(); int[] pitches = phrase switch {
            "bass" => [36, 43, 46, 48], "drums" => [36, 38, 42, 38], "melody" => [60, 64, 67, 72], _ => throw new ArgumentException() };
        for (int i = 0; i < pitches.Length; ++i) events.Add(new { kind = "note", tick = i * 480, duration = phrase == "drums" ? 120 : 420, pitch = pitches[i], velocity = 90 });
        using var native = new NativeEngine();
        native.Call(new { op = "edit.batch", expected_revision = native.Revision, commands = new[] {
            new { op = "track.add", track = new { id = "preview", channel = 0, end_tick = 1920, events } } } });
        return Convert.FromBase64String(native.Call(new { op = "midi.export", playback = true }).GetProperty("data_base64").GetString()!);
    }
}
