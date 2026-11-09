# Native editing protocol (0.1)

The C++ library is the authoritative project, revision and Undo/Redo owner.
The CLI, MCP host and future GUI call the same engine. The native protocol below
is JSON, **not an independently implemented MCP transport**. File access,
permissions, audio workers and MCP framing belong to the host adapters.

## Build and ABI

Build requires CMake 3.20+, a C++20 compiler, and nlohmann/json headers. The
dependency is not downloaded automatically or vendored in this repository.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
ctest --test-dir build --output-on-failure -C Release
```

If the dependency has no CMake package configuration, supply
`-DJSON_INCLUDE_DIR=/path/to/nlohmann-json/include`.

The shared library exports these C-compatible functions from
[`core.h`](../include/cantabile/core.h):

```c
void* cantabile_create(void);
void cantabile_destroy(void* engine);
char* cantabile_request(void* engine, const char* request_json);
void cantabile_free(char* response_json);
```

Requests and responses are UTF-8 JSON strings. The caller owns the request and
must release each returned string with `cantabile_free`, including error
responses. `cantabile_create` or `cantabile_request` can return null on allocation
failure. No C++ object, exception or allocator ownership crosses this ABI.
Requests on an instance are serialized; callers must stop before destroying it.

## Project schema

```json
{
  "schema_version": 1,
  "name": "First song",
  "ppq": 480,
  "smf_format": 1,
  "tracks": [
    {
      "id": "melody",
      "name": "Melody",
      "channel": 0,
      "events": [
        {"id":"tempo","tick":0,"kind":"meta","meta_type":81,"data":[7,161,32]},
        {"id":"program","tick":0,"kind":"midi","status":192,"data":[0]},
        {"id":"note-1","tick":0,"kind":"note","pitch":60,"duration":480,"velocity":100,"release_velocity":32}
      ]
    }
  ]
}
```

`schema_version`, `ppq` and `tracks` are required when loading. Missing project
name defaults to `Untitled`; format defaults to 1. Missing track names default
to `MIDI Track`, channels to 0, and event arrays to empty. Track/event IDs are
assigned if absent. Existing IDs must be unique throughout the project, 1–128
ASCII letters/digits or `- _ . :`. Generated IDs should always be read back from
the returned project, not predicted by clients.

Musical time is a nonnegative integer tick. `ppq` is 1–32767 ticks per quarter
note. The optional track `end_tick` preserves the end-of-track silence; export
extends it to contain all events/notes rather than truncating data. Track
`instrument` is an opaque persisted object; the core does not load a plugin or
interpret a host-specific sound path. Other project/track metadata is preserved.

| Event kind | Required fields | Notes |
| --- | --- | --- |
| `note` | `pitch` 0–127, `duration` >=1, `velocity` 1–127 | Optional `release_velocity` 0–127 and `channel` 0–15; track channel is the default |
| `midi` | `status` 128–239, `data` array | Exact channel-message status and one/two 7-bit data bytes |
| `meta` | `meta_type` 0–127, `data` byte array | Opaque bytes, including non-UTF-8 text, remain intact; EOT 47 is managed separately |
| `sysex` | `status` 240 or 247, `data` byte array | Preserves SMF F0/F7 form and payload |

Every event has `id`, `tick`, `kind`, and optionally integer `order`. Raw imported
events preserve same-tick order. New composed notes support `off_order` when an
explicit end ordering is needed; by default their note-offs precede new note-ons
at a shared boundary. MIDI channels are zero-based (0–15); standard GM percussion
uses channel 9. A raw message's channel is encoded in its status and is not
silently changed by a track's default channel property.

### MIDI compatibility boundary

SMF Type 0 and 1 with PPQ timing are supported. Import expands running status but
preserves channel-message meaning, all meta/SysEx payload bytes, event order and
end-of-track position. **Imported note-on/off messages remain raw `midi` events**
so overlapping/unmatched notes, velocity-zero note-on, and release velocity are
not guessed or paired incorrectly. Newly composed `note` events expand to
note-on/off on export. RPN/NRPN/14-bit controllers are preserved/editable as their
underlying CC messages; there is no dedicated parameter editor yet.

Export is deterministic canonical SMF, not a byte-for-byte reproduction of
running-status compression. Format 0 requires one track. An empty project can
export a valid empty one-track file. A fresh nonempty track name becomes a Track
Name meta event if absent. Explicit `track.update` name patches update the first
existing naming event; untouched imported name payloads are preserved.

Type 2, SMPTE timing, extended headers/unknown chunks, malformed VLQs/statuses,
missing or malformed EOT, and trailing chunk bytes are explicitly rejected.
No unsupported payload is silently dropped. Delta-times larger than SMF's
four-byte VLQ range are rejected on export, even though the project tick range
can be larger. MIDI 2.0/UMP and live hardware routing are not implemented.

## Requests and transactions

`project.get` returns the current project and revision. Every mutation requires
an exact `expected_revision`; a mismatch produces `REVISION_CONFLICT` without
changing state. Revisions start at zero and increase on every successful load,
new/import, nonempty edit batch, undo and redo. Revisions never move backwards.

Mutation requests may include `max_project_bytes` (1–33554432). The serialized
UTF-8 candidate project is checked against that caller budget **before** changing
project, revision or history, including Undo/Redo restores. The native default is
32 MiB; a host with a smaller persistence/response limit must pass its own budget
on every mutation. This prevents a valid edit from producing an unsavable project.

```json
{"op":"project.get"}
{"op":"project.load","expected_revision":0,"data":{"schema_version":1,"ppq":480,"tracks":[]}}
{"op":"edit.batch","expected_revision":1,"commands":[{"op":"track.add","track":{"id":"melody","name":"Melody","channel":0,"events":[]}}]}
{"op":"history.undo","expected_revision":2}
{"op":"history.redo","expected_revision":3}
{"op":"midi.export","expected_revision":4}
```

The examples are separate newline-delimited requests, not one JSON document.

| Operation | Request fields / behavior |
| --- | --- |
| `project.get` | Read current state; does not mutate or require a guard |
| `project.new` | `expected_revision`; creates an empty project and clears history |
| `project.load` | `expected_revision`, `data` project object; validates before replacement and clears history |
| `midi.import` | `expected_revision`, `data_base64` containing a complete SMF; clears history only on success |
| `edit.batch` | `expected_revision`, `commands` array; all commands commit as one history entry or none do |
| `history.undo` / `history.redo` | `expected_revision`; restores state as a new revision |
| `midi.export` | Optional `expected_revision`, `format` 0/1, `playback` boolean; does not mutate |

An empty edit batch is a no-op after guard validation. Loading/importing a
different document clears prior history rather than allowing Undo across files.
New edits after Undo clear Redo. Undo/Redo uses bounded in-memory snapshots and
is not persisted in the project file in this version.

Successful state responses contain `ok`, `revision`, `project`, `can_undo`, and
`can_redo`. MIDI export responses contain `ok`, `revision`, `data_base64`, and
`size`. Error responses are `{ "ok": false, "error": { "code": "...",
"message": "..." } }`. Clients should branch on codes rather than prose.

Ordinary MIDI export includes all tracks/events. `playback:true` creates a
non-mutating performance copy: muted tracks and nonsolo tracks (when any Solo is
set) contribute no channel/note/SysEx events. Their meta events and duration are
retained so muting a track cannot delete the song's tempo/meter map.

### Edit commands

| Command | Fields |
| --- | --- |
| `track.add` | `track` object; ID/defaults can be assigned by the core |
| `track.remove` | `track_id` |
| `track.update` | `track_id`, `patch` with `name`, `channel`, `instrument`, `muted`, `solo`, `end_tick` |
| `event.add` | `track_id`, `event` object |
| `event.remove` | `track_id`, `event_id` |
| `event.update` | `track_id`, `event_id`, `patch` with supported event fields; ID cannot change |
| `project.update` | `patch` with `name`, `ppq`, `smf_format` |
| `events.transpose` | `track_id`, `semitones` -127…127, optional `start_tick`/`end_tick` |
| `events.quantize` | `track_id`, `grid` >=1, optional `start_tick`/`end_tick` |

Transforms use a half-open tick range. Transpose adjusts composed note pitch and
raw note-on/off/poly-pressure pitches, failing the whole batch on out-of-range
notes. Quantize rounds composed note starts and raw note-on/off ticks to the
nearest grid; composed durations are preserved. Tie positions round upward.
Other controller/meta events are unchanged. Changing `ppq` does not rescale
event ticks. Explicit event edits can change controller/meta/SysEx data without
requiring high-level interpretations of every MIDI extension.

## CLI

`cantabile-cli --stdio` maintains one native session and reads one JSON request
per LF-terminated line. It outputs one compact JSON response per request, flushes
each response, and keeps malformed requests from killing the session. Oversized
lines are drained and rejected. EOF in an incomplete frame is an error.

```sh
cantabile-cli new song.json
cantabile-cli inspect song.json
cantabile-cli validate song.json
cantabile-cli edit song.json commands.json
cantabile-cli export-midi song.json song.mid
cantabile-cli import-midi song.mid restored.json
```

`commands.json` is either an array of edit commands or an object containing a
`commands` array. File CLI commands use a fresh process/session, so history and
revision are session-local. Completed file outputs publish through a same-folder
temporary file and atomic replacement; no partially written destination is
published. This low-level local CLI does not replace the MCP host's workspace
permissions, project locks, or lifecycle guards.

The file CLI applies a **32 MiB − 4096 byte** compact-project budget to every
mutation through its request helper, including `request REQUEST.json`. A smaller
explicit budget is respected; requesting the native 32 MiB budget cannot bypass
the file CLI cap. Saved JSON is compact with no extra trailing newline. The
4096-byte reserve ensures the saved project fits inside a later native
`project.load` request envelope, so a successful edit/save can be reopened.
The direct C ABI and the `--stdio` native session retain their separate native
limits; the MCP host applies its own smaller workspace budget.

## Resource bounds

- 32 MiB maximum request/project JSON, and at most 64 JSON nesting levels.
- 16 MiB maximum MIDI input/output, 256 tracks, 100000 events.
- 1 MiB maximum individual event byte payload in the editing model.
- 10000 edit commands per batch; 64 entries / 64 MiB per history stack.
- Integer project ticks and order values are bounded by 2^53−1; SMF deltas
  additionally obey the 28-bit VLQ limit.

An exported composed note counts as two MIDI events; generated track-name/EOT
events also count toward the export bound. Error codes include `INVALID_REQUEST`,
`INVALID_PROJECT`, `INVALID_MIDI`, `MIDI_EXPORT_FAILED`, `REVISION_CONFLICT`,
`NOT_FOUND`, `NO_HISTORY`, `LIMIT_EXCEEDED`, and `UNSUPPORTED_OP`.
