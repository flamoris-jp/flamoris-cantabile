using System.Reflection;
using System.Text.Json;
using Flamoris.Cantabile.Host;
using Flamoris.Mcp.Core;

// Links the production supervisor. No Kontakt, plugin, native core or test package.
internal static class Program
{
    private const int Maximum = 16384;
    private const int KontaktStderrMaximum = 1024 * 1024;
    private const string PrivateText = "private-vendor-text";

    public static async Task<int> Main(string[] args)
    {
        if (args[0] == "--child")
        {
            string mode = args[1];
            if (mode == "crash") return 17;
            if (mode == "malformed-success") { Console.Write(PrivateText + "{\"ok\":true}"); return 0; }
            if (mode == "malformed-object") { Console.Write("  {" + PrivateText); return 0; }
            if (mode == "wait") { await Task.Delay(60000); return 0; }
            if (mode.StartsWith("kontakt-stderr-", StringComparison.Ordinal))
            {
                int size = mode == "kontakt-stderr-small" ? Maximum + 19 : KontaktStderrMaximum;
                if (mode == "kontakt-stderr-overflow") size++;
                Console.Error.Write(PrivateText + new string('x', size - PrivateText.Length));
                Console.Error.Flush();
                if (mode == "kontakt-stderr-overflow") { await Task.Delay(60000); return 0; }
                Console.Write(mode == "kontakt-stderr-failure"
                    ? "{\"ok\":false,\"error\":{\"code\":\"KONTAKT_LOAD_FAILED\"}}" : "{\"ok\":true}");
                return mode == "kontakt-stderr-failure" ? 1 : 0;
            }
            var stream = mode.StartsWith("stdout", StringComparison.Ordinal) ? Console.Out : Console.Error;
            bool overflow = mode.EndsWith("overflow", StringComparison.Ordinal);
            if (mode == "stdout-boundary") Console.Write("{\"ok\":true}".PadRight(Maximum));
            else stream.Write((PrivateText + new string('x', Maximum)).AsSpan(0, overflow ? Maximum + 1 : Maximum));
            stream.Flush();
            if (overflow) { await Task.Delay(60000); return 0; }
            if (mode == "stderr-boundary") Console.Write("{\"ok\":true}");
            return 0;
        }
        string executable = Path.GetFullPath(args[0]);
        string assembly = Assembly.GetExecutingAssembly().Location;
        Task<JsonElement> Run(string mode, TimeSpan? timeout = null, CancellationToken token = default, int stderrMaximum = Maximum) =>
            WorkerProcess.Run(executable, [assembly, "--child", mode], Environment.CurrentDirectory,
                timeout ?? TimeSpan.FromSeconds(15), token, Maximum, stderrMaximum);
        foreach (string mode in new[] { "stdout-boundary", "stderr-boundary" })
            Check((await Run(mode)).GetProperty("ok").GetBoolean(), mode);
        foreach (string stream in new[] { "stdout", "stderr" })
        {
            try { await Run(stream + "-overflow"); throw new Exception("Overflow was accepted"); }
            catch (WorkerFailure failure)
            {
                Check(failure.Code == "vst_output_limit", "failure code");
                var limit = failure.Summary.GetProperty("output_limit");
                Check(limit.GetProperty("stream").GetString() == stream, "stream identity");
                Check(limit.GetProperty("maximum_characters").GetInt32() == Maximum, "unchanged bound");
                int observed = limit.GetProperty("observed_characters").GetInt32();
                Check(observed > Maximum && observed <= Maximum + 4096, "bounded observed count");
                var termination = failure.Summary.GetProperty("termination");
                Check(!termination.GetProperty("exited_before_cleanup").GetBoolean(), "worker was alive");
                Check(termination.GetProperty("host_kill_requested").GetBoolean(), "host requested termination");
                Check(termination.GetProperty("exit_code").ValueKind == JsonValueKind.Number, "worker exit observed");
                Check(!failure.Summary.GetRawText().Contains(PrivateText, StringComparison.Ordinal), "vendor text privacy");
            }
        }
        foreach (string mode in new[] { "kontakt-stderr-small", "kontakt-stderr-boundary" })
            Check((await Run(mode, stderrMaximum: KontaktStderrMaximum)).GetProperty("ok").GetBoolean(), mode);
        foreach (var test in new[] { (Mode: "kontakt-stderr-overflow", Stream: "stderr", Limit: KontaktStderrMaximum),
                                    (Mode: "stdout-overflow", Stream: "stdout", Limit: Maximum) })
        {
            try { await Run(test.Mode, stderrMaximum: KontaktStderrMaximum); throw new Exception("Overflow was accepted"); }
            catch (WorkerFailure failure)
            {
                Check(failure.Code == "vst_output_limit", "Kontakt overflow classification");
                var limit = failure.Summary.GetProperty("output_limit");
                Check(limit.GetProperty("stream").GetString() == test.Stream, "independent stream bounds");
                Check(limit.GetProperty("maximum_characters").GetInt32() == test.Limit, "Kontakt hard bound");
                Check(limit.GetProperty("observed_characters").GetInt32() > test.Limit, "discarded stderr still counted");
                Check(failure.Summary.GetProperty("termination").GetProperty("host_kill_requested").GetBoolean(), "overflow termination");
                Check(!failure.Summary.GetRawText().Contains(PrivateText, StringComparison.Ordinal), "discarded stderr privacy");
            }
        }
        try { await Run("kontakt-stderr-failure", stderrMaximum: KontaktStderrMaximum); throw new Exception("Failure was accepted"); }
        catch (WorkerFailure failure) { Check(failure.Code == "KONTAKT_LOAD_FAILED", "diagnostics do not mask failure"); }
        try { await Run("wait", TimeSpan.FromMilliseconds(300)); throw new Exception("Timeout was accepted"); }
        catch (McpFault fault) { Check(fault.Code == "vst_timeout", "timeout classification"); }
        using (var cancel = new CancellationTokenSource(300))
        {
            try { await Run("wait", token: cancel.Token); throw new Exception("Cancellation was accepted"); }
            catch (OperationCanceledException) { Check(cancel.IsCancellationRequested, "caller cancellation"); }
        }
        foreach (string mode in new[] { "crash", "malformed-success", "malformed-object" })
        {
            try { await Run(mode); throw new Exception("Invalid worker result was accepted"); }
            catch (WorkerFailure failure)
            {
                Check(failure.Code == "vst_worker_crashed", "compatible result failure code");
                var channel = failure.Summary.GetProperty("result_channel");
                Check(channel.GetProperty("kind").GetString() == "invalid_json", "result channel classification");
                Check(channel.GetProperty("exit_code").GetInt32() == (mode == "crash" ? 17 : 0), "exit code preserved");
                Check(channel.GetProperty("stdout_empty").GetBoolean() == (mode == "crash"), "empty output distinguished");
                Check(channel.GetProperty("stdout_starts_with_object").GetBoolean() == (mode == "malformed-object"), "JSON object prefix");
                Check(channel.GetProperty("stdout_characters").GetInt32() >= 0, "bounded stdout count");
                Check(channel.GetProperty("stderr_characters").GetInt32() == 0, "bounded stderr count");
                Check(!failure.Summary.GetRawText().Contains(PrivateText, StringComparison.Ordinal), "result channel privacy");
            }
        }
        Console.WriteLine("PASS: default/Kontakt bounds, diagnostics, malformed results, exit codes, privacy, timeout and cancellation");
        return 0;
    }
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }
}

// Minimal compile-time dependency for the linked supervisor only.
namespace Flamoris.Mcp.Core
{
    internal sealed class McpFault(string code) : Exception(code)
    {
        public string Code { get; } = code;
    }
}
