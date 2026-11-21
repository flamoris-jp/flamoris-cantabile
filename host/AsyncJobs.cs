using System.Text.Json;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

// Product jobs reuse Core authorization/guards at begin/status/cancel; execution
// belongs to the host lifetime, never to a short-lived transport request.
public sealed class AsyncJobs : IAsyncDisposable
{
    private readonly object gate = new();
    private readonly Dictionary<string, Job> jobs = new(StringComparer.Ordinal);
    private bool disposed;
    public JsonElement Begin(string kind, long revision, Func<Action<int, int>, CancellationToken, Task<JsonElement>> work)
    {
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(disposed, this);
            if (jobs.Values.Any(j => j.Kind == kind && !j.Task.IsCompleted)) throw new McpFault("busy");
            if (jobs.Count >= 32)
            {
                var old = jobs.Values.FirstOrDefault(j => j.Task.IsCompleted) ?? throw new McpFault("job_limit");
                jobs.Remove(old.Id); old.Cancellation.Dispose();
            }
            var job = new Job(kind, revision); jobs.Add(job.Id, job);
            job.Task = Task.Run(async () => {
                try
                {
                    lock (gate) { job.Cancellation.Token.ThrowIfCancellationRequested(); job.State = "running"; }
                    var result = await work((done, total) => { lock (gate) { job.Done = done; job.Total = total; } }, job.Cancellation.Token);
                    // Work owns atomic publication and its final cancellation check.
                    lock (gate) { job.Result = result; job.State = "completed"; }
                }
                catch (OperationCanceledException) { lock (gate) { job.State = "cancelled"; job.Error = Unknown("cancelled"); } }
                catch (Exception e) { lock (gate) { job.State = "failed"; job.Error = e is WorkerFailure f ? f.Summary : Unknown(e is McpFault m ? m.Code : "job_failed"); } }
            });
            return View(job);
        }
    }
    private static JsonElement Unknown(string code) => JsonSerializer.SerializeToElement(new { code,
        rollback = new { attempted = (bool?)null, restored = (bool?)null, status = "unknown" } });
    public JsonElement Status(string id) { lock (gate) return View(Find(id)); }
    public JsonElement Cancel(string id) { lock (gate) { var job = Find(id); if (!job.Task.IsCompleted) job.Cancellation.Cancel(); return View(job); } }
    private Job Find(string id) => jobs.TryGetValue(id, out var job) ? job : throw new McpFault("unknown_job");
    private static JsonElement View(Job j) => JsonSerializer.SerializeToElement(new { job_id = j.Id, kind = j.Kind, revision = j.Revision,
        state = j.State, completed_units = j.Done, total_units = j.Total, result = j.Result, error = j.Error });
    public async ValueTask DisposeAsync()
    {
        Task[] pending; lock (gate) { disposed = true; foreach (var j in jobs.Values) j.Cancellation.Cancel(); pending = jobs.Values.Select(j => j.Task).ToArray(); }
        await Task.WhenAll(pending); foreach (var j in jobs.Values) j.Cancellation.Dispose();
    }
    private sealed class Job(string kind, long revision)
    {
        public string Id { get; } = Guid.NewGuid().ToString("N");
        public string Kind { get; } = kind;
        public long Revision { get; } = revision;
        public string State = "queued";
        public int Done, Total;
        public JsonElement? Result, Error;
        public CancellationTokenSource Cancellation { get; } = new();
        public Task Task = Task.CompletedTask;
    }
}
