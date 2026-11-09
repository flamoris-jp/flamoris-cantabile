using System.Globalization;
using System.Text.Json;
using Flamoris.Mcp.Core;
using ModelContextProtocol.Protocol;
using ModelContextProtocol.Server;

namespace Flamoris.Cantabile.Host;

/// <summary>Official SDK framing over stdio; shared Core still authorizes every product tool.</summary>
internal static class SdkStdio
{
    public static async Task RunAsync(EditorHost host, CapabilityGrant grant, CancellationToken token)
    {
        await using var input = new BoundedInput(Console.OpenStandardInput(), host.Boundary.Options.MaxRequestBytes);
        await using var transport = new StreamServerTransport(input, Console.OpenStandardOutput(), "flamoris.cantabile");
        var catalog = Program.Catalog(host);
        var tools = catalog.Select(item => new Tool { Name = item!["name"]!.GetValue<string>(),
            Description = item["description"]!.GetValue<string>(),
            InputSchema = JsonSerializer.SerializeToElement(item["input_schema"]), }).ToList();
        var options = new McpServerOptions {
            ServerInfo = new() { Name = "flamoris.cantabile", Version = "0.1.0" }, ScopeRequests = false,
            InitializationTimeout = TimeSpan.FromSeconds(10), Capabilities = new() { Tools = new() },
            Handlers = new() {
                ListToolsHandler = (request, ct) => {
                    ct.ThrowIfCancellationRequested(); grant.Revoked.ThrowIfCancellationRequested();
                    if (request.Params?.Cursor is not null) throw new ModelContextProtocol.McpException("invalid_request");
                    return ValueTask.FromResult(new ListToolsResult { Tools = tools.Where(t => t.Name == "mcp.context" || grant.Permission == McpPermission.Edit || host.Boundary.Tools[t.Name].Kind == OperationKind.Query).ToList() });
                },
                CallToolHandler = async (request, ct) => {
                    using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(token, ct, grant.Revoked);
                    try {
                        string name = request.Params?.Name ?? "";
                        var args = JsonSerializer.SerializeToElement(request.Params?.Arguments ?? new Dictionary<string, JsonElement>());
                        if (name == "mcp.context") {
                            if (args.EnumerateObject().Any()) throw new McpFault(McpErrors.InvalidRequest);
                            return ToWire(new(await host.InvokeAsync(() => {
                                if (!grant.IsActive) throw new McpFault(McpErrors.Unauthorized);
                                var s = host.Snapshot;
                                return JsonSerializer.SerializeToElement(new { productId = s.ProductId, applicationVersion = s.ApplicationVersion, runtimeId = s.RuntimeId,
                                    documentToken = s.DocumentToken, revision = s.Revision.ToString(CultureInfo.InvariantCulture), available = s.Available,
                                    hasDocument = s.HasDocument, busy = s.HumanEditing, permission = grant.Permission == McpPermission.Edit ? "edit" : "readOnly" });
                            }, lifetime.Token)));
                        }
                        if (args.EnumerateObject().Any(p => p.Name is not ("input" or "guard")) || !args.TryGetProperty("input", out var value)) throw new McpFault(McpErrors.InvalidRequest);
                        var guard = args.TryGetProperty("guard", out var g) ? Guard(g) : null;
                        return ToWire(await host.Boundary.InvokeAsync(grant, name, value, guard, lifetime.Token));
                    }
                    catch (McpFault fault) { return ToWire(McpResult.Failure(fault.Code)); }
                    catch (OperationCanceledException) { return ToWire(McpResult.Failure(McpErrors.Cancelled)); }
                    catch (Exception e) when (e is JsonException or ArgumentException or InvalidOperationException or FormatException) { return ToWire(McpResult.Failure(McpErrors.InvalidRequest)); }
                },
            },
        };
        await using var server = McpServer.Create(transport, options);
        await server.RunAsync(token);
    }
    private static RequestGuard Guard(JsonElement json)
    {
        if (json.ValueKind != JsonValueKind.Object || json.EnumerateObject().Any(p => p.Name is not ("runtimeId" or "documentToken" or "expectedRevision"))) throw new McpFault(McpErrors.InvalidRequest);
        string Read(string field) => json.TryGetProperty(field, out var v) && v.ValueKind == JsonValueKind.String && v.GetString() is { Length: >= 1 and <= 128 } text ? text : throw new McpFault(McpErrors.InvalidRequest);
        long? revision = null;
        if (json.TryGetProperty("expectedRevision", out _)) {
            var value = Read("expectedRevision");
            if (!long.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out var n) || n < 0 || n.ToString(CultureInfo.InvariantCulture) != value) throw new McpFault(McpErrors.InvalidRequest);
            revision = n;
        }
        return new(Read("runtimeId"), Read("documentToken"), revision);
    }
    private static CallToolResult ToWire(McpResult result)
    {
        var value = result.Error is { } code ? JsonSerializer.SerializeToElement(new { error = new { code } }) : result.Value ?? JsonSerializer.SerializeToElement(new { });
        return new() { IsError = result.IsError, StructuredContent = value, Content = [new TextContentBlock { Text = value.GetRawText() }] };
    }
    // Bounds JSON-lines before the SDK parses an untrusted client frame. No custom JSON-RPC implementation.
    private sealed class BoundedInput(Stream inner, int maximum) : Stream
    {
        private int frame;
        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default) {
            int n = await inner.ReadAsync(buffer, cancellationToken);
            foreach (byte b in buffer.Span[..n]) { frame = b == (byte)'\n' ? 0 : checked(frame + 1); if (frame > maximum) throw new InvalidDataException("MCP frame exceeds limit."); }
            return n;
        }
        public override int Read(byte[] buffer, int offset, int count) => ReadAsync(buffer.AsMemory(offset, count)).AsTask().GetAwaiter().GetResult();
        public override bool CanRead => true; public override bool CanSeek => false; public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException(); public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override void Flush() => throw new NotSupportedException(); public override long Seek(long o, SeekOrigin origin) => throw new NotSupportedException(); public override void SetLength(long v) => throw new NotSupportedException(); public override void Write(byte[] b, int o, int c) => throw new NotSupportedException();
    }
}
