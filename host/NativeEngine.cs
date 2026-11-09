using System.Runtime.InteropServices;
using System.Text.Json;
using Flamoris.Mcp.Core;

namespace Flamoris.Cantabile.Host;

/// <summary>Only the native session owns project state, revisions and history.</summary>
public sealed class NativeEngine : IDisposable
{
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate IntPtr Create();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate void Destroy(IntPtr session);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate IntPtr Request(IntPtr session, IntPtr json);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate void Free(IntPtr value);
    private readonly IntPtr library;
    private IntPtr session;
    private readonly Destroy destroy;
    private readonly Request request;
    private readonly Free free;
    public long Revision { get; private set; }

    public NativeEngine()
    {
        var path = Environment.GetEnvironmentVariable("CANTABILE_CORE_LIBRARY");
        library = string.IsNullOrWhiteSpace(path) ? NativeLibrary.Load("cantabile_core") : NativeLibrary.Load(Path.GetFullPath(path));
        try
        {
            T Load<T>(string name) where T : Delegate => Marshal.GetDelegateForFunctionPointer<T>(NativeLibrary.GetExport(library, name));
            destroy = Load<Destroy>("cantabile_destroy"); request = Load<Request>("cantabile_request"); free = Load<Free>("cantabile_free");
            session = Load<Create>("cantabile_create")();
            if (session == IntPtr.Zero) throw new InvalidOperationException("Native session creation failed.");
            Call(new { op = "project.get" });
        }
        catch { if (session != IntPtr.Zero) { destroy!(session); session = IntPtr.Zero; } NativeLibrary.Free(library); throw; }
    }
    public JsonElement Call(object command)
    {
        ObjectDisposedException.ThrowIf(session == IntPtr.Zero, this);
        var payload = System.Text.Json.Nodes.JsonNode.Parse(JsonSerializer.Serialize(command))!.AsObject();
        if (payload["op"]?.GetValue<string>() is not ("project.get" or "midi.export")) payload["max_project_bytes"] = Workspace.MaximumProjectBytes;
        var json = payload.ToJsonString();
        if (System.Text.Encoding.UTF8.GetByteCount(json) > 4 * 1024 * 1024) throw new McpFault("request_too_large");
        IntPtr input = Marshal.StringToCoTaskMemUTF8(json), result = IntPtr.Zero;
        try
        {
            result = request(session, input);
            if (result == IntPtr.Zero) throw new McpFault("native_error");
            var text = Marshal.PtrToStringUTF8(result) ?? throw new McpFault("native_error");
            using var document = JsonDocument.Parse(text, new() { MaxDepth = 64 });
            var value = document.RootElement;
            if (!value.TryGetProperty("ok", out var ok) || !ok.GetBoolean())
            {
                var code = value.TryGetProperty("error", out var error) && error.TryGetProperty("code", out var c) ? c.GetString() : "native_error";
                throw new McpFault(code ?? "native_error");
            }
            if (value.TryGetProperty("revision", out var revision)) Revision = revision.GetInt64();
            return value.Clone();
        }
        finally { if (result != IntPtr.Zero) free(result); Marshal.FreeCoTaskMem(input); }
    }
    public void Dispose()
    {
        if (session == IntPtr.Zero) return;
        destroy(session); session = IntPtr.Zero; NativeLibrary.Free(library);
    }
}
