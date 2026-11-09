using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

public sealed class RenderJobs : IAsyncDisposable
{
    private readonly Workspace workspace;
    private readonly string? renderer = Environment.GetEnvironmentVariable("CANTABILE_RENDERER");
    private readonly string? soundfont = Environment.GetEnvironmentVariable("CANTABILE_SOUNDFONT");
    private readonly Dictionary<string, Job> jobs = new(StringComparer.Ordinal);
    private readonly object gate = new();
    private bool disposed;
    public RenderJobs(Workspace workspace) => this.workspace = workspace;
    public object Instruments() => new { backends = new[] { new { id = "fluidsynth", available = Available,
        soundfont = "configured", instrument_selection = "MIDI bank/program events" } }, vst3 = "not_implemented" };
    public bool Available => !string.IsNullOrEmpty(renderer) && File.Exists(renderer) && !string.IsNullOrEmpty(soundfont) && File.Exists(soundfont);
    public JsonElement Begin(byte[] midi, long revision, int maximumSeconds)
    {
        if (maximumSeconds is < 1 or > 600) throw new McpFault("invalid_range");
        if (!Available) throw new McpFault("audio_backend_unavailable");
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(disposed, this);
            if (jobs.Values.Any(j => j.State is "queued" or "running")) throw new McpFault("busy");
            if (jobs.Count >= 64)
            {
                var terminal = jobs.Values.FirstOrDefault(j => j.Task.IsCompleted);
                if (terminal is null) throw new McpFault("job_limit");
                jobs.Remove(terminal.Id); terminal.Cancellation.Dispose();
            }
            var job = new Job(Guid.NewGuid().ToString("N"), revision);
            string input = workspace.TempFile(".mid"), output = workspace.TempFile(".wav");
            try { using var file = new FileStream(input, FileMode.CreateNew, FileAccess.Write, FileShare.None); file.Write(midi); }
            catch { File.Delete(input); throw; }
            jobs.Add(job.Id, job);
            job.Task = RunAsync(job, input, output, maximumSeconds);
            return StatusLocked(job);
        }
    }
    public JsonElement Status(string id)
    { lock (gate) { if (!jobs.TryGetValue(id, out var job)) throw new McpFault("unknown_job"); return StatusLocked(job); } }
    public JsonElement Cancel(string id)
    {
        lock (gate)
        {
            if (!jobs.TryGetValue(id, out var job)) throw new McpFault("unknown_job");
            if (job.State is "queued" or "running") job.Cancellation.Cancel(); return StatusLocked(job);
        }
    }
    private static JsonElement StatusLocked(Job job) => JsonSerializer.SerializeToElement(new { job_id = job.Id, revision = job.Revision,
        state = job.State, artifact = job.Artifact, audio = job.Audio, error = job.Error });
    private async Task RunAsync(Job job, string input, string output, int maximumSeconds)
    {
        await Task.Yield();
        try
        {
            lock (gate) { job.Cancellation.Token.ThrowIfCancellationRequested(); job.State = "running"; }
            using var process = new Process { StartInfo = new ProcessStartInfo(Path.GetFullPath(renderer!)) { RedirectStandardOutput = true,
                RedirectStandardError = true, UseShellExecute = false, CreateNoWindow = true } };
            foreach (var arg in new[] { "--midi", input, "--soundfont", Path.GetFullPath(soundfont!), "--output", output,
                "--max-seconds", maximumSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture), "--backend", "fluidsynth" }) process.StartInfo.ArgumentList.Add(arg);
            // Credentials are not inherited by third-party renderers.
            var inherited = process.StartInfo.Environment;
            string[] retained = ["PATH", "LD_LIBRARY_PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL"];
            var safeEnvironment = inherited.Where(p => retained.Contains(p.Key, StringComparer.OrdinalIgnoreCase)).ToArray();
            inherited.Clear(); foreach (var pair in safeEnvironment) inherited[pair.Key] = pair.Value;
            if (!process.Start()) throw new McpFault("render_start_failed");
            using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(job.Cancellation.Token);
            lifetime.CancelAfter(TimeSpan.FromSeconds(maximumSeconds + 30));
            Task<string> stdout = ReadTextAsync(process.StandardOutput, 16384, lifetime.Token);
            Task<string> stderr = ReadTextAsync(process.StandardError, 16384, lifetime.Token);
            try
            {
                var pending = new List<Task> { process.WaitForExitAsync(lifetime.Token), stdout, stderr };
                while (pending.Count > 0) { var done = await Task.WhenAny(pending); await done; pending.Remove(done); }
            }
            catch
            {
                try { process.Kill(entireProcessTree: true); await process.WaitForExitAsync(CancellationToken.None); } catch (InvalidOperationException) { }
                try { await Task.WhenAll(stdout, stderr); } catch { }
                throw;
            }
            using var result = JsonDocument.Parse(await stdout, new() { MaxDepth = 16 });
            if (process.ExitCode != 0 || !result.RootElement.TryGetProperty("ok", out var ok) || !ok.GetBoolean()) throw new McpFault("render_failed");
            var audio = result.RootElement;
            // Retain curated measurements only, not the renderer's filesystem paths.
            var measurements = JsonSerializer.SerializeToElement(new { duration_seconds = audio.GetProperty("duration_seconds").GetDouble(),
                sample_rate = audio.GetProperty("sample_rate").GetInt32(), channels = audio.GetProperty("channels").GetInt32(),
                frames = audio.GetProperty("frames").GetInt64(), peak = audio.GetProperty("peak").GetDouble(), clipped_samples = audio.GetProperty("clipped_samples").GetInt64() });
            lock (gate)
            {
                job.Cancellation.Token.ThrowIfCancellationRequested();
                job.Artifact = workspace.PublishFile(output, ".wav", "audio/wav", job.Revision);
                job.Audio = measurements; job.State = "completed";
            }
        }
        catch (OperationCanceledException) { lock (gate) { job.State = job.Cancellation.IsCancellationRequested ? "cancelled" : "failed"; job.Error = job.State == "failed" ? "render_timeout" : null; } }
        catch (Exception e) { lock (gate) { job.State = "failed"; job.Error = e is McpFault fault ? fault.Code : "render_failed"; } }
        finally { File.Delete(input); File.Delete(output); File.Delete(output + ".part"); }
    }
    private static async Task<string> ReadTextAsync(StreamReader reader, int maximum, CancellationToken token)
    {
        var result = new StringBuilder(); char[] buffer = new char[1024]; int n;
        while ((n = await reader.ReadAsync(buffer.AsMemory(), token)) > 0) { if (result.Length + n > maximum) throw new McpFault("render_output_limit"); result.Append(buffer, 0, n); }
        return result.ToString();
    }
    public async ValueTask DisposeAsync()
    {
        Task[] tasks;
        lock (gate) { disposed = true; foreach (var job in jobs.Values) job.Cancellation.Cancel(); tasks = jobs.Values.Select(j => j.Task).ToArray(); }
        await Task.WhenAll(tasks);
        foreach (var job in jobs.Values) job.Cancellation.Dispose();
    }
    private sealed class Job(string id, long revision)
    {
        public string Id { get; } = id;
        public long Revision { get; } = revision;
        public string State = "queued";
        public string? Error;
        public JsonElement? Artifact;
        public JsonElement? Audio;
        public CancellationTokenSource Cancellation { get; } = new();
        public Task Task = Task.CompletedTask;
    }
}
