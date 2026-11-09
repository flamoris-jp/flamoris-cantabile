using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

public sealed partial class Workspace : IDisposable
{
    public const int MaximumProjectBytes = 2 * 1024 * 1024;
    public const int MaximumMidiBytes = 1024 * 1024;
    public const int MaximumChunkBytes = 128 * 1024;
    public const long MaximumArtifactBytes = 128L * 1024 * 1024;
    public const long MaximumTotalArtifactBytes = 512L * 1024 * 1024;
    private readonly string root, projects, exports;
    private readonly FileStream ownership;
    private readonly Dictionary<string, Artifact> artifacts = new(StringComparer.Ordinal);
    private readonly object artifactGate = new();
    public string ActiveName { get; private set; } = "resident.cantabile.json";
    private string? savedDigest;
    private bool disposed;
    public Workspace(string path)
    {
        root = Path.GetFullPath(path); VerifyParents(root);
        Directory.CreateDirectory(root); VerifyParents(root);
        projects = Path.Combine(root, "projects"); exports = Path.Combine(root, "exports");
        foreach (var directory in new[] { projects, exports }) { VerifyParents(directory); Directory.CreateDirectory(directory); VerifyParents(directory); }
        string lockPath = Path.Combine(root, "cantabile.lock"); DemandRegular(lockPath);
        ownership = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        try { RecoverArtifacts(); } catch { ownership.Dispose(); throw; }
    }
    [GeneratedRegex("^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\\.cantabile\\.json\\z", RegexOptions.CultureInvariant)]
    private static partial Regex ProjectName();
    private string ProjectPath(string name)
    {
        if (!ProjectName().IsMatch(name) || name.Contains("..", StringComparison.Ordinal)) throw new McpFault("invalid_filename");
        VerifyParents(projects); var path = Path.Combine(projects, name); DemandRegular(path); return path;
    }
    public (JsonElement Project, string Digest)? ReadProject(string name)
    {
        string path = ProjectPath(name); if (!File.Exists(path)) return null;
        byte[] bytes = ReadBounded(path, MaximumProjectBytes);
        using var json = JsonDocument.Parse(bytes, new() { MaxDepth = 64 });
        return (json.RootElement.Clone(), Digest(bytes));
    }
    public void Opened(string name, string digest) { _ = ProjectPath(name); ActiveName = name; savedDigest = digest; }
    public object Save(JsonElement project, string? saveAs = null)
    {
        string name = saveAs ?? ActiveName, path = ProjectPath(name);
        if (saveAs is not null && File.Exists(path)) throw new McpFault("file_exists");
        if (saveAs is null && File.Exists(path))
        {
            string digest = Digest(ReadBounded(path, MaximumProjectBytes));
            if (savedDigest is null || digest != savedDigest) throw new McpFault("file_changed");
        }
        else if (saveAs is null && savedDigest is not null) throw new McpFault("file_changed");
        byte[] bytes = System.Text.Encoding.UTF8.GetBytes(project.GetRawText());
        if (bytes.Length > MaximumProjectBytes) throw new McpFault("project_too_large");
        AtomicWrite(path, bytes, overwrite: saveAs is null);
        ActiveName = name; savedDigest = Digest(bytes);
        return new { saved = true, name, size = bytes.Length, sha256 = savedDigest };
    }
    public object ListProjects()
    {
        VerifyParents(projects);
        return new { active = ActiveName, files = Directory.EnumerateFiles(projects).Where(p => ProjectName().IsMatch(Path.GetFileName(p)) && !IsLink(p))
            .Take(257).Select(p => new { name = Path.GetFileName(p), size = new FileInfo(p).Length }).ToArray() };
    }
    public string TempFile(string extension)
    {
        VerifyParents(exports); CheckQuota();
        if (extension is not (".mid" or ".wav")) throw new McpFault("invalid_request");
        return Path.Combine(exports, ".pending-" + Guid.NewGuid().ToString("N") + extension);
    }
    public JsonElement PublishBytes(byte[] bytes, string extension, string mimeType, long revision)
    {
        if (bytes.Length > MaximumArtifactBytes) throw new McpFault("artifact_too_large");
        string pending = TempFile(extension);
        try { AtomicWrite(pending, bytes, overwrite: false); return PublishFile(pending, extension, mimeType, revision); }
        finally { File.Delete(pending); }
    }
    public JsonElement PublishFile(string pending, string extension, string mimeType, long revision)
    {
        lock (artifactGate) return PublishFileLocked(pending, extension, mimeType, revision);
    }
    private JsonElement PublishFileLocked(string pending, string extension, string mimeType, long revision)
    {
        VerifyParents(exports); DemandRegular(pending); CheckQuota();
        if (Path.GetDirectoryName(pending) != exports || !Path.GetFileName(pending).StartsWith(".pending-", StringComparison.Ordinal)) throw new McpFault("invalid_artifact");
        var info = new FileInfo(pending); if (!info.Exists || info.Length > MaximumArtifactBytes) throw new McpFault("artifact_too_large");
        if (Directory.EnumerateFiles(exports).Sum(p => IsLink(p) ? 0 : new FileInfo(p).Length) > MaximumTotalArtifactBytes) throw new McpFault("workspace_quota");
        string id = Guid.NewGuid().ToString("N"), destination = Path.Combine(exports, id + extension);
        string digest; using (var stream = File.OpenRead(pending)) digest = Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
        File.Move(pending, destination, false);
        var final = new FileInfo(destination);
        var artifact = new Artifact(id, destination, mimeType, final.Length, digest, final.LastWriteTimeUtc, revision);
        try { WriteManifest(artifact); artifacts.Add(id, artifact); } catch { File.Delete(destination); throw; }
        return Metadata(artifact);
    }
    public JsonElement ReadArtifact(string id, long offset, int maximum)
    {
        lock (artifactGate) return ReadArtifactLocked(id, offset, maximum);
    }
    private JsonElement ReadArtifactLocked(string id, long offset, int maximum)
    {
        if (!artifacts.TryGetValue(id, out var artifact)) throw new McpFault("unknown_artifact");
        if (offset < 0 || offset > artifact.Size || maximum is < 1 or > MaximumChunkBytes) throw new McpFault("invalid_range");
        VerifyParents(exports); DemandRegular(artifact.Path);
        var info = new FileInfo(artifact.Path);
        if (!info.Exists || info.Length != artifact.Size || info.LastWriteTimeUtc != artifact.Modified) throw new McpFault("artifact_changed");
        byte[] bytes = new byte[(int)Math.Min(maximum, artifact.Size - offset)];
        using (var stream = File.OpenRead(artifact.Path)) { stream.Position = offset; stream.ReadExactly(bytes); }
        return JsonSerializer.SerializeToElement(new { file_id = id, offset, next_offset = offset + bytes.Length,
            complete = offset + bytes.Length == artifact.Size, data_base64 = Convert.ToBase64String(bytes), chunk_sha256 = Digest(bytes),
            size = artifact.Size, sha256 = artifact.Digest, mime_type = artifact.MimeType, revision = artifact.Revision });
    }
    private void CheckQuota()
    {
        lock (artifactGate)
        {
            var files = Directory.EnumerateFiles(exports).Take(301).ToArray();
            if (files.Length >= 300 || artifacts.Count >= 128 || files.Sum(p => IsLink(p) ? 0 : new FileInfo(p).Length) >= MaximumTotalArtifactBytes) throw new McpFault("workspace_quota");
        }
    }
    public JsonElement ListArtifacts()
    {
        lock (artifactGate) return JsonSerializer.SerializeToElement(new { files = artifacts.Values.Select(Metadata).ToArray() });
    }
    public JsonElement ReleaseArtifact(string id)
    {
        lock (artifactGate)
        {
            if (!artifacts.TryGetValue(id, out var artifact)) throw new McpFault("unknown_artifact");
            VerifyParents(exports); DemandRegular(artifact.Path);
            string manifest = Path.Combine(exports, id + ".artifact.json"); DemandRegular(manifest);
            File.Delete(artifact.Path); File.Delete(manifest); artifacts.Remove(id);
            return JsonSerializer.SerializeToElement(new { file_id = id, released = true });
        }
    }
    private void WriteManifest(Artifact a) => AtomicWrite(Path.Combine(exports, a.Id + ".artifact.json"),
        JsonSerializer.SerializeToUtf8Bytes(new { file_id = a.Id, size = a.Size, sha256 = a.Digest, mime_type = a.MimeType, revision = a.Revision }), overwrite: true);
    private void RecoverArtifacts()
    {
        // A completed immutable payload can be recovered even if the process stopped
        // between renaming it and writing its metadata. Never follow an arbitrary path.
        foreach (var path in Directory.EnumerateFiles(exports).Where(p => Path.GetExtension(p) is ".mid" or ".wav").Take(129))
        {
            string id = Path.GetFileNameWithoutExtension(path);
            if (!Guid.TryParseExact(id, "N", out _)) continue;
            DemandRegular(path); var info = new FileInfo(path);
            if (info.Length > MaximumArtifactBytes || artifacts.Count >= 128) throw new McpFault("workspace_quota");
            string digest; using (var input = File.OpenRead(path)) digest = Convert.ToHexString(SHA256.HashData(input)).ToLowerInvariant();
            string mime = Path.GetExtension(path) == ".mid" ? "audio/midi" : "audio/wav";
            long revision = -1;
            string manifest = Path.Combine(exports, id + ".artifact.json");
            if (File.Exists(manifest))
            {
                using var json = JsonDocument.Parse(ReadBounded(manifest, 4096)); var metadata = json.RootElement;
                if (metadata.GetProperty("file_id").GetString() != id || metadata.GetProperty("sha256").GetString() != digest
                    || metadata.GetProperty("size").GetInt64() != info.Length || metadata.GetProperty("mime_type").GetString() != mime)
                    throw new McpFault("artifact_changed");
                revision = metadata.GetProperty("revision").GetInt64();
            }
            var artifact = new Artifact(id, path, mime, info.Length, digest, info.LastWriteTimeUtc, revision);
            if (!File.Exists(manifest)) WriteManifest(artifact);
            artifacts.Add(id, artifact);
        }
    }
    private static JsonElement Metadata(Artifact a) => JsonSerializer.SerializeToElement(new { file_id = a.Id, size = a.Size, sha256 = a.Digest, mime_type = a.MimeType, revision = a.Revision });
    private sealed record Artifact(string Id, string Path, string MimeType, long Size, string Digest, DateTime Modified, long Revision);
    public static byte[] ReadBounded(string path, int maximum)
    {
        DemandRegular(path); using var stream = File.OpenRead(path);
        if (stream.Length > maximum) throw new McpFault("file_too_large");
        using var output = new MemoryStream(); byte[] buffer = new byte[8192]; int count;
        while ((count = stream.Read(buffer)) != 0) { if (output.Length + count > maximum) throw new McpFault("file_too_large"); output.Write(buffer, 0, count); }
        return output.ToArray();
    }
    private static void AtomicWrite(string path, byte[] bytes, bool overwrite)
    {
        DemandRegular(path); string temporary = Path.Combine(Path.GetDirectoryName(path)!, ".writing-" + Guid.NewGuid().ToString("N"));
        try
        {
            using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { output.Write(bytes); output.Flush(true); }
            DemandRegular(path); VerifyParents(Path.GetDirectoryName(path)!); File.Move(temporary, path, overwrite);
        }
        finally { File.Delete(temporary); }
    }
    public static string Digest(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    public static void VerifyParents(string path)
    {
        for (var current = new DirectoryInfo(Path.GetFullPath(path)); current is not null; current = current.Parent)
            if (current.LinkTarget is not null || (current.Exists && (current.Attributes & FileAttributes.ReparsePoint) != 0)) throw new McpFault("symlink_forbidden");
    }
    private static bool IsLink(string path) => new FileInfo(path).LinkTarget is not null || ((File.Exists(path) || Directory.Exists(path)) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0);
    public static void DemandRegular(string path) { if (IsLink(path) || Directory.Exists(path)) throw new McpFault("symlink_forbidden"); }
    public void Dispose() { if (disposed) return; disposed = true; ownership.Dispose(); }
}
