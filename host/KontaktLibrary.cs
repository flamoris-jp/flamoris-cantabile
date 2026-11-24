using System.Text;
using System.Text.Json;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

// Metadata discovery only: never copies or decodes a commercial patch/sample.
// The local operator chooses bounded content roots; MCP supplies opaque IDs.
internal sealed class KontaktLibrary
{
    internal sealed record Item(string Id, string Name, string Kind, string Path);
    internal sealed record Library(string Id, string Name, string Path);
    internal sealed record Selection(string LibraryId, Item Instrument, Item? Snapshot);
    private readonly string? configuration = Environment.GetEnvironmentVariable("CANTABILE_KONTAKT_LIBRARIES");
    private static void Closed(JsonElement value, params string[] keys)
    {
        if (value.ValueKind != JsonValueKind.Object) throw new McpFault("kontakt_configuration_invalid");
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var p in value.EnumerateObject()) if (!keys.Contains(p.Name) || !seen.Add(p.Name)) throw new McpFault("kontakt_configuration_invalid");
    }
    private static string Text(JsonElement value, string key, int maximum)
    {
        if (!value.TryGetProperty(key, out var field) || field.ValueKind != JsonValueKind.String ||
            field.GetString() is not { Length: > 0 } text || text.Length > maximum || text.Any(char.IsControl))
            throw new McpFault("kontakt_configuration_invalid");
        return text;
    }
    private Library[] Libraries()
    {
        if (string.IsNullOrEmpty(configuration)) return [];
        try
        {
            Workspace.VerifyParents(Path.GetDirectoryName(Path.GetFullPath(configuration))!);
            using var json = JsonDocument.Parse(Workspace.ReadBounded(configuration, 64 * 1024), new() { MaxDepth = 8 });
            var root = json.RootElement; Closed(root, "version", "libraries");
            if (root.GetProperty("version").GetInt32() != 1) throw new McpFault("kontakt_configuration_invalid");
            var items = root.GetProperty("libraries");
            if (items.ValueKind != JsonValueKind.Array || items.GetArrayLength() > 32) throw new McpFault("kontakt_configuration_invalid");
            var result = new List<Library>();
            foreach (var item in items.EnumerateArray())
            {
                Closed(item, "id", "name", "root");
                string id = Text(item, "id", 64), name = Text(item, "name", 128), path = Text(item, "root", 8192);
                if (id.Length is < 1 or > 64 || id.Any(c => !char.IsAsciiLetterOrDigit(c) && c is not '-' and not '_') ||
                    name.Length is < 1 or > 128 || name.Any(char.IsControl) || !Path.IsPathFullyQualified(path) || result.Any(x => x.Id == id))
                    throw new McpFault("kontakt_configuration_invalid");
                path = Path.TrimEndingDirectorySeparator(Path.GetFullPath(path));
                if (path == Path.GetPathRoot(path)) throw new McpFault("kontakt_configuration_invalid");
                result.Add(new(id, name, path));
            }
            return result.ToArray();
        }
        catch (McpFault) { throw; }
        catch (Exception e) when (e is JsonException or IOException or UnauthorizedAccessException or ArgumentException or InvalidOperationException or KeyNotFoundException)
        { throw new McpFault("kontakt_configuration_invalid"); }
    }
    private static Item[] Scan(Library library)
    {
        Workspace.VerifyParents(library.Path);
        if (!Directory.Exists(library.Path)) throw new McpFault("kontakt_library_missing");
        var pending = new Stack<(string Path, int Depth)>(); pending.Push((library.Path, 0));
        int visited = 0; var items = new List<Item>();
        try
        {
            while (pending.TryPop(out var next))
            {
                if (next.Depth > 12) throw new McpFault("kontakt_catalog_limit");
                Workspace.VerifyParents(next.Path);
                foreach (var entry in new DirectoryInfo(next.Path).EnumerateFileSystemInfos())
                {
                    if (++visited > 32768) throw new McpFault("kontakt_catalog_limit");
                    if ((entry.Attributes & FileAttributes.ReparsePoint) != 0) continue;
                    if ((entry.Attributes & FileAttributes.Directory) != 0) { pending.Push((entry.FullName, next.Depth + 1)); continue; }
                    string kind = entry.Extension.ToLowerInvariant() switch { ".nki" => "instrument", ".nksn" => "snapshot", _ => "" };
                    if (kind.Length == 0) continue;
                    if (items.Count >= 2048) throw new McpFault("kontakt_catalog_limit");
                    string relative = Path.GetRelativePath(library.Path, entry.FullName).Replace('\\', '/');
                    if (relative.Length > 512 || relative.Any(char.IsControl)) continue;
                    string id = "kontakt-" + Workspace.Digest(Encoding.UTF8.GetBytes(library.Id + "\n" + relative));
                    items.Add(new(id, relative, kind, entry.FullName));
                }
            }
            return items.OrderBy(x => x.Name, StringComparer.Ordinal).ToArray();
        }
        catch (IOException) { throw new McpFault("kontakt_library_unreadable"); }
        catch (UnauthorizedAccessException) { throw new McpFault("kontakt_library_unreadable"); }
    }
    internal JsonElement List(string? libraryId, int offset, int limit)
    {
        var libraries = Libraries();
        if (libraryId is null) return JsonSerializer.SerializeToElement(new {
            libraries = libraries.Select(x => new { library_id = x.Id, display_name = x.Name }),
            configured = libraries.Length != 0, scope = "operator_registered_roots", platform_supported = OperatingSystem.IsWindows() });
        var library = libraries.SingleOrDefault(x => x.Id == libraryId) ?? throw new McpFault("kontakt_library_unknown");
        var items = Scan(library);
        return JsonSerializer.SerializeToElement(new { library_id = library.Id, display_name = library.Name,
            items = items.Skip(offset).Take(limit).Select(x => new { content_id = x.Id, display_name = x.Name, kind = x.Kind }),
            total = items.Length, next_offset = offset + limit < items.Length ? (int?)(offset + limit) : null,
            pairing = "snapshot_must_be_selected_for_compatible_instrument", license_verified = false });
    }
    internal Selection Select(string libraryId, string instrumentId, string? snapshotId)
    {
        var library = Libraries().SingleOrDefault(x => x.Id == libraryId) ?? throw new McpFault("kontakt_library_unknown");
        var items = Scan(library);
        var instrument = items.SingleOrDefault(x => x.Id == instrumentId && x.Kind == "instrument") ?? throw new McpFault("kontakt_instrument_unknown");
        var snapshot = snapshotId is null ? null : items.SingleOrDefault(x => x.Id == snapshotId && x.Kind == "snapshot") ?? throw new McpFault("kontakt_snapshot_unknown");
        foreach (var item in new[] { instrument, snapshot }.OfType<Item>())
        { Workspace.VerifyParents(Path.GetDirectoryName(item.Path)!); Workspace.DemandRegular(item.Path); if (!File.Exists(item.Path)) throw new McpFault("kontakt_content_missing"); }
        return new(libraryId, instrument, snapshot);
    }
}
