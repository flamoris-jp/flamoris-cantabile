# Cantabile headless MCP host (0.1)

This .NET 10 adapter attaches to one authoritative native C++ session through the
C ABI. It does not maintain another project model or Undo/Redo stack.

`Flamoris.Mcp.Core` **1.4.0** provides typed tool adapters, permissions, revision
and session guards, cancellation, and the existing outbound Hub connector. Its
transitive official `ModelContextProtocol.Core` package supplies the MCP protocol.
`Flamoris.Logging` supplies bounded file diagnostics. Application state and file
operations remain owned by Cantabile.

The local stdio route uses the official SDK's `StreamServerTransport` directly
and delegates product calls to the same `McpBoundary`. It requires no socket,
HTTP listener, separate editing process, or local capability environment variable.
Hub mode uses the shared Core `HubConnectionProvider` reverse-WSS protocol.

## Build and run

Build the native targets first, then:

```sh
dotnet restore host/Flamoris.Cantabile.Host.csproj
dotnet build host/Flamoris.Cantabile.Host.csproj -c Release --no-restore

export CANTABILE_CORE_LIBRARY="$PWD/build/libcantabile_core.so"
export CANTABILE_RENDERER="$PWD/build/audio/cantabile-render"
# Supply an installed SoundFont whose software/asset terms you have reviewed.
export CANTABILE_SOUNDFONT=/absolute/path/to/bank.sf2

dotnet host/bin/Release/net10.0/cantabile-host.dll stdio \
  --workspace /absolute/path/to/cantabile-data --permission edit
```

The workspace must be private, application-owned local storage. It is created
when absent; symlinked workspace parents are rejected. Only one host owns a
workspace at a time through an exclusive lock. A read-only MCP grant advertises
queries only and rejects all writes. The default grant is `readOnly`; the caller
must explicitly choose `edit` to permit editing and file output.

MCP stdout contains protocol messages only. Application diagnostics use stderr
and bounded `logs/mcp.log` files (2 MiB each, three retained files). Project
payloads, credentials, and raw process errors are not logged.

### Hub service

```sh
dotnet host/bin/Release/net10.0/cantabile-host.dll serve \
  --workspace /absolute/path/to/cantabile-data --permission edit \
  --hub-endpoint wss://hub.example.com/desktop/cantabile \
  --credential-file /absolute/path/to/private-connector-token \
  --health-port 8774
```

The example endpoint is a placeholder. Supply an actual configured Hub upstream
and its credential. On Linux, the credential file must exclude all group/other
permissions, for example mode `0600`. Credentials are read at connection time;
they are never put in a project, tool input, catalog, or process argument value.
The endpoint must be WSS without userinfo, query, or fragment.

The optional service health listener uses the configured port (default 8774) and
binds **127.0.0.1 only**. `GET /healthz` reports process readiness separately from
Hub connection and configured audio availability. Process health does not certify
remote Hub registration, instrument functionality, or client audio listening.
There is no direct public HTTP MCP listener.

The shared connector has bounded retries; the host refreshes it after exhaustion.
A deployed 18-tool Hub round trip and fresh Docker/Updater installation have been
accepted; see [acceptance evidence](../docs/ACCEPTANCE.md). Building this source alone
does not establish a new deployment's acceptance. Compatible updates and controlled
production restart/recovery remain separate tests.

### Static catalog

```sh
dotnet host/bin/Release/net10.0/cantabile-host.dll catalog \
  --workspace /absolute/path/to/temporary-catalog-workspace
```

This outputs the actual complete tool input schemas plus effect annotations.
Use it to pin the matching Hub catalog; do not guess schemas from this guide.

## MCP tools

Every product call uses the shared Core wrapper:

```json
{
  "input": {"commands": [{"op":"track.add","track":{"id":"melody","name":"Melody","channel":0,"events":[]}}]},
  "guard": {"runtimeId":"from-context","documentToken":"from-context","expectedRevision":"0"}
}
```

Call `mcp.context` with `{}` first. Mutations require all three guard fields and
a canonical decimal-string revision. Read tools may omit the guard. A stale
runtime, document, or revision is rejected before the native commit. The native
engine independently receives the current expected revision.

| Tool | Input / behavior |
| --- | --- |
| `mcp.context` | Current identity, permission, document token, and revision |
| `project_get` | `{}`; native project and live history availability |
| `project_load` | `data` project object and required `discard_unsaved` boolean; validates before replacement |
| `edit_batch` | `commands` array, 1–256 closed native edit commands; one atomic native history entry |
| `history_undo`, `history_redo` | `{}`; native shared history |
| `project_list` | `{}`; configured workspace project names |
| `project_save` | `{}` saves the active project, optional `name` is **Save As a new file** |
| `project_open` | `name` and required `discard_unsaved`; opens one saved workspace project |
| `midi_import` | `data_base64` containing a complete SMF, plus required `discard_unsaved` |
| `midi_export` | `{}`; immutable MIDI artifact with `file_id`, size and SHA-256 |
| `file_list` | `{}`; retained immutable exports, including restart recovery |
| `file_read` | `file_id`, `offset`, optional `max_bytes`; base64 bytes, next offset and chunk/full SHA-256 |
| `file_release` | `file_id`; explicitly deletes one exported artifact/manifest to reclaim quota; never deletes project files |
| `instruments_list` | `{}`; actual configured backend availability and current VST limitation |
| `audio_render` | Optional `max_seconds` 1–600; snapshots MIDI with mute/solo playback filtering and returns a job ID |
| `audio_status` | `job_id`; terminal state, curated audio measurements, immutable WAV artifact metadata |
| `audio_cancel` | `job_id`; cancels an active owned job; completed artifacts stay available |

Native event, project, and command schemas are documented in
[PROTOCOL.md](../docs/PROTOCOL.md). MIDI import/export is performed by the native
engine, never by a second .NET MIDI editor.

## Persistence and boundaries

- Projects are saved compactly under `projects/`. The startup project is
  `resident.cantabile.json` when it exists; other files can be opened explicitly.
- Changes stay in memory until `project_save`. Shutdown does not silently
  overwrite a previously saved project. Undo/Redo is in-memory for this version.
- Save As requires a new basename ending `.cantabile.json`, with ASCII letters,
  digits, dots, underscores, or hyphens; paths, traversal, and symlinks are rejected.
- Updating an existing active file checks its last-read/saved digest. An external
  change is reported as `file_changed`, rather than overwritten.
- Project files and native mutation candidates are limited to **2 MiB compact
  UTF-8 JSON**. The native core rejects an over-budget candidate before commit,
  including otherwise valid edit batches and Undo/Redo states.
- A complete uploaded MIDI is limited to **1 MiB**. File downloads use chunks of
  at most **128 KiB**, with whole-file and per-chunk SHA-256 metadata.
- Completed exports are immutable and retained under `exports/`. A persisted
  manifest binds their size/digest/type/revision. On restart, payloads and
  manifests are revalidated; a crash between payload publication and manifest
  publication recovers the payload with unknown original revision (`-1`).
- Export resources are bounded: 128 registered artifacts, 128 MiB per artifact,
  512 MiB of total export data. Use `file_list` and `file_release` to reclaim space.
- Render job history retains at most 64 jobs; the oldest completed metadata can
  be evicted. Its completed artifact remains available through `file_list`.

The workspace is an application-owned trust boundary, not a defense against a
malicious same-user process changing its files concurrently. Filesystem guards
prevent MCP clients from choosing arbitrary paths or following configured
symlinks. Save and publication use same-directory atomic rename.

## Audio behavior

The first renderer is **FluidSynth with one host-configured SoundFont**. It is a
free-software, device-free offline rendering path. It is not a VST host.

Instrument metadata can be persisted by the native editor for later adapters,
but current audio rendering accepts only no instrument object, an empty object,
or `{ "backend": "fluidsynth" }`. Unsupported backends or settings are rejected
instead of silently ignored. Set GM instrument bank/program through MIDI events.

Rendering uses `midi.export` with `playback:true`, preserving conductor meta data
while suppressing muted/nonsolo channel performance. Ordinary MIDI export keeps
all tracks. A worker uses a fixed configured executable and SoundFont path, a
bounded output capture and deadline, and an allowlisted environment. It never
receives MCP credentials or arbitrary caller-chosen executable arguments.

Completed WAV bytes are retrieved using `file_read`. This first host does **not**
claim native MCP audio blocks or model audio input: Core 1.4's rich-result API
currently exposes images, not audio. Client playback and actual model listening
must be verified independently after retrieving and presenting the audio.

## Verification

```sh
python3 -m unittest discover -s host/tests -v
```

The integration suite drives the real official SDK stdio server, shared Core
permission/revision checks, native edit history and codec, safe persistence,
restart recovery, immutable file retrieval, and actual FluidSynth WAV rendering
when the worker/SoundFont are available. `CANTABILE_DOTNET`,
`CANTABILE_CORE_LIBRARY`, `CANTABILE_RENDERER`, and `CANTABILE_SOUNDFONT` can select
locally built test dependencies. No live infrastructure is needed.
