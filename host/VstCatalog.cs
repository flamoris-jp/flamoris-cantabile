using System.Text;
using System.Text.Json;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

public sealed class VstCatalog
{
    private readonly Workspace workspace;
    private readonly string? worker = Environment.GetEnvironmentVariable("CANTABILE_RENDERER");
    private readonly TimeSpan workerTimeout = ScanTimeout();
    private readonly object gate = new();
    private List<Entry> entries = [];
    private List<Module> modules = [];
    private static TimeSpan ScanTimeout()
    {
        string? value = Environment.GetEnvironmentVariable("CANTABILE_VST3_SCAN_TIMEOUT_MS");
        if (string.IsNullOrEmpty(value)) return TimeSpan.FromSeconds(30);
        if (!int.TryParse(value, out int milliseconds) || milliseconds is < 1 or > 30000) throw new ArgumentException("Invalid VST scan timeout.");
        return TimeSpan.FromMilliseconds(milliseconds);
    }
    public sealed record Entry(string Id, string ModuleId, string Path, string ClassId, string Name, string Vendor,
        string Version, string Kind, bool Loaded, bool Usable, JsonElement? Buses, string? InspectionError, bool? LastRenderAudible = null);
    private sealed record Module(string Id, string Path, bool Detected, bool Loaded, string? Error);
    public VstCatalog(Workspace workspace)
    {
        this.workspace = workspace;
        if (workspace.ReadVstCatalog() is { } saved)
        {
            entries = saved.GetProperty("entries").Deserialize<List<Entry>>() ?? [];
            modules = saved.GetProperty("modules").Deserialize<List<Module>>() ?? [];
            if (entries.Count > 2048 || modules.Count > 512) throw new McpFault("catalog_limit");
        }
    }
    public JsonElement List()
    {
        lock (gate) return JsonSerializer.SerializeToElement(new { supported_formats = new[] { "vst3" },
            unsupported_formats = new[] { "vst2", "au", "aax" },
            instruments = entries.Select(e => new { catalog_id = e.Id, module_id = e.ModuleId, class_id = e.ClassId,
                name = e.Name, vendor = e.Vendor, version = e.Version, kind = e.Kind, detected = Exists(e.Path),
                loaded = e.Loaded, usable = e.Usable && Exists(e.Path), buses = e.Buses,
                inspection_error = e.InspectionError, last_render_audible = e.LastRenderAudible }),
            modules = modules.Select(m => new { module_id = m.Id, detected = Exists(m.Path), loaded = m.Loaded, error = m.Error }) });
    }
    public Entry Resolve(string id)
    {
        lock (gate)
        {
            var entry = entries.FirstOrDefault(e => e.Id == id) ?? throw new McpFault("instrument_missing");
            if (!Exists(entry.Path)) throw new McpFault("instrument_missing");
            if (!entry.Usable || !entry.Loaded || entry.Kind != "instrument") throw new McpFault("instrument_unusable");
            return entry;
        }
    }
    private static bool Exists(string path) => File.Exists(path) || Directory.Exists(path);
    internal void RecordAudible(string id)
    {
        lock (gate)
        {
            var next = entries.Select(e => e.Id == id ? e with { LastRenderAudible = true } : e).ToList();
            workspace.SaveVstCatalog(new { version = 1, entries = next, modules }); entries = next;
        }
    }
    private static string BoundedText(JsonElement value, string key)
    {
        if (!value.TryGetProperty(key, out var field) || field.ValueKind != JsonValueKind.String) return "";
        string text = field.GetString()!;
        return new string(text.Where(c => !char.IsControl(c)).Take(256).ToArray());
    }
    public async Task<JsonElement> Scan(Action<int, int> progress, CancellationToken token)
    {
        if (!File.Exists(worker)) throw new McpFault("vst_backend_unavailable");
        string sandbox = workspace.CreateVstSandbox();
        try
        {
            var paths = Discover(token); progress(0, paths.Count);
            // Keep previous discoveries as explicit missing/uninspected records;
            // failed scans never silently remove a saved project's reference.
            List<Entry> next; List<Module> nextModules;
            lock (gate) { next = entries.Select(e => e with { Loaded = false, Usable = false, InspectionError = "not_scanned" }).ToList(); nextModules = []; }
            int done = 0;
            foreach (string path in paths)
            {
                token.ThrowIfCancellationRequested();
                string moduleId = Workspace.Digest(Encoding.UTF8.GetBytes(path))[..24];
                try
                {
                    var result = await WorkerProcess.Run(worker!, ["list", "--plugin", path], sandbox, workerTimeout, token);
                    var classes = result.GetProperty("classes");
                    if (classes.GetArrayLength() > 256) throw new McpFault("catalog_limit");
                    nextModules.Add(new(moduleId, path, true, true, null));
                    foreach (var item in classes.EnumerateArray())
                    {
                        string classId = BoundedText(item, "id");
                        if (classId.Length != 32 || !classId.All(char.IsAsciiHexDigit)) continue;
                        string id = "vst3-" + classId.ToUpperInvariant();
                        // First detected copy wins deterministically. Other physical
                        // copies do not overwrite a class selected earlier this scan.
                        if (next.Any(e => e.Id == id && e.InspectionError != "not_scanned")) continue;
                        string kind = BoundedText(item, "kind"); bool loaded = false, usable = false; JsonElement? buses = null; string? error = null;
                        try
                        {
                            var report = await WorkerProcess.Run(worker!, ["inspect", "--plugin", path, "--class-id", classId], sandbox, workerTimeout, token);
                            var rawBuses = report.GetProperty("buses");
                            if (rawBuses.GetArrayLength() > 256) throw new McpFault("catalog_limit");
                            var safeBuses = rawBuses.EnumerateArray().Select(b => new { media_type = BoundedText(b, "media_type"),
                                direction = BoundedText(b, "direction"), index = b.GetProperty("index").GetInt32(),
                                channel_count = b.GetProperty("channel_count").GetInt32(), bus_type = b.GetProperty("bus_type").GetInt32() }).ToArray();
                            buses = JsonSerializer.SerializeToElement(safeBuses); loaded = true;
                            usable = kind == "instrument" && safeBuses.Any(b => b.media_type == "event" && b.direction == "input" && b.index == 0 && b.channel_count > 0) &&
                                safeBuses.Any(b => b.media_type == "audio" && b.direction == "output" && b.index == 0 && b.bus_type == 0 && b.channel_count is 1 or 2) &&
                                report.GetProperty("render_supported").GetBoolean();
                        }
                        catch (OperationCanceledException) { throw; }
                        catch (Exception e) { error = e is WorkerFailure f ? f.Code : e is McpFault m ? m.Code : "inspection_failed"; }
                        next.RemoveAll(e => e.Id == id);
                        if (next.Count >= 2048) throw new McpFault("catalog_limit");
                        next.Add(new(id, moduleId, path, classId, BoundedText(item, "name"), BoundedText(item, "vendor"), BoundedText(item, "version"), kind,
                            loaded, usable, buses, error));
                    }
                }
                catch (OperationCanceledException) { throw; }
                catch (Exception e) { nextModules.RemoveAll(m => m.Id == moduleId); nextModules.Add(new(moduleId, path, true, false,
                    e is WorkerFailure f ? f.Code : e is McpFault m ? m.Code : "scan_failed")); }
                lock (gate)
                {
                    token.ThrowIfCancellationRequested();
                    workspace.SaveVstCatalog(new { version = 1, entries = next, modules = nextModules });
                    entries = next.ToList(); modules = nextModules.ToList();
                }
                progress(++done, paths.Count);
            }
            lock (gate)
            {
                token.ThrowIfCancellationRequested();
                workspace.SaveVstCatalog(new { version = 1, entries = next, modules = nextModules }); entries = next; modules = nextModules;
            }
            return List();
        }
        finally { Directory.Delete(sandbox, recursive: true); }
    }
    private static List<string> Discover(CancellationToken token)
    {
        var roots = new List<string>();
        if (OperatingSystem.IsWindows())
        {
            foreach (string name in new[] { "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432" })
                if (Environment.GetEnvironmentVariable(name) is { Length: > 0 } folder) roots.Add(Path.Combine(folder, "VST3"));
            roots.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Common", "VST3"));
        }
        else if (OperatingSystem.IsLinux()) roots.AddRange(["/usr/lib/vst3", "/usr/local/lib/vst3", Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".vst3")]);
        else throw new McpFault("vst_platform_unsupported");
        roots.AddRange((Environment.GetEnvironmentVariable("CANTABILE_VST3_PATHS") ?? "").Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries));
        if (Environment.GetEnvironmentVariable("CANTABILE_VST3_MODULE") is { Length: > 0 } legacy) roots.Add(legacy);
        var paths = new HashSet<string>(OperatingSystem.IsWindows() ? StringComparer.OrdinalIgnoreCase : StringComparer.Ordinal);
        var pending = new Stack<(string Path, int Depth)>(roots.Select(p => (Path.GetFullPath(p), 0))); int visited = 0;
        while (pending.TryPop(out var item))
        {
            token.ThrowIfCancellationRequested(); if (++visited > 32768) throw new McpFault("scan_limit");
            if (!Exists(item.Path)) continue;
            if ((File.GetAttributes(item.Path) & FileAttributes.ReparsePoint) != 0) continue;
            if (Path.GetExtension(item.Path).Equals(".vst3", StringComparison.OrdinalIgnoreCase))
            { paths.Add(item.Path); if (paths.Count > 512) throw new McpFault("scan_limit"); continue; }
            if (!Directory.Exists(item.Path) || item.Depth >= 12) continue;
            try { foreach (string child in Directory.EnumerateFileSystemEntries(item.Path).Take(32769)) pending.Push((child, item.Depth + 1)); }
            catch (UnauthorizedAccessException) { }
        }
        return paths.Order(StringComparer.Ordinal).ToList();
    }
}
