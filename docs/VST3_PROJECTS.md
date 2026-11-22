# Catalog-backed VST3 projects (development, Issue #10)

Issue #12 adds a [private sound shelf](SOUND_SHELF.md). Its assignments embed
standard state and optional sound ID, plugin version and loading conditions in
the native project; existing projects retain their previous render behavior.

This is an implemented development contract, separate from the published v0.1.0
preview and from commercial-instrument workstation acceptance. It supports one
instrument instance per musical track, sequential isolated rendering, aligned
stems and a complete mix. It does not establish production band acceptance until
the workstation checklist below has been completed.

## Ownership and compatibility

The C++ native project remains the authority for assignments, standard sound
state, MIDI/channel settings, gain, pan, mute/solo and Undo/Redo. The host owns its
private installed-plugin registry, immutable artifacts and background jobs. Hub
only routes the product tools. Core 1.4 authorization, edit permissions and
revision guards remain in use. Core has no general product-job implementation;
these jobs use Cantabile's existing begin/status/cancel pattern, with work bound
to the host lifetime rather than an individual MCP request deadline.

Project schema 1 gains optional fields; old schema-1 files remain readable and
do not acquire an implicit assignment. Legacy opaque instrument objects remain
round-trippable but are not catalog instances. If any track has `catalog_id`,
`audio_render` uses the project renderer and every active musical track must have
a usable assignment. If no track has a catalog assignment, the existing configured
FluidSynth/single-VST backend remains the compatibility path. The response from
`instruments_list` describes this selection rule explicitly.

New catalog instrument objects are closed:

```json
{
  "id": "keys",
  "channel": 2,
  "render_channel": 0,
  "volume": 0.8,
  "pan": -0.25,
  "muted": false,
  "solo": false,
  "instrument": {
    "backend": "vst3",
    "catalog_id": "vst3-0123456789ABCDEF0123456789ABCDEF"
  },
  "events": []
}
```

`state_base64` is an optional instrument field containing a standard `.vstpreset`
snapshot, including component/controller state. It is embedded in the native
project, independent of export-artifact retention. There is no required preset:
an instrument whose default state works can render immediately. A state snapshot
is limited to 1 MiB per instance and the host's existing aggregate 2 MiB project
budget still applies; oversized states fail explicitly. The low-level native
project limit is unchanged (32 MiB). State capture does not authorize publishing
commercial presets to source control.

`channel` and per-event channels retain their existing MIDI meaning.
`render_channel` (0..15), when present, remaps notes and raw channel messages only
in that track's render snapshot. Exported interchange MIDI and the source events
are unchanged. Set it to `null` in `track.update` to remove the override. The
renderer rejects channels outside the instrument's first event input instead
of silently losing notes. For single-channel plugins, an explicit override of
0 is often needed when the score uses different MIDI channels for different parts.

`volume` is a linear gain (0..4, default 1), `pan` is stereo balance (-1..1,
default 0). At center both channels retain unity gain; moving left attenuates
only the right channel linearly and vice versa. These fields and `render_channel`
use `edit_batch` / `track.update`, the existing guards, and native history.

## Discover and choose installed plugins

Set `CANTABILE_RENDERER` to the CantabileClient worker. Catalog mode does not
require a single `CANTABILE_VST3_MODULE`. The optional legacy module is also a
discovery candidate. Legacy class and preset settings remain optional and are
not applied to catalog assignments.

`vst_scan {}` starts an edit-authorized asynchronous scan. Poll
`vst_job_status {"job_id":"..."}` or cancel with `vst_job_cancel`.

Discovery roots are:

| Platform | Standard roots |
| --- | --- |
| Windows x64 | Common Program Files VST3 locations and Local AppData/Programs/Common/VST3 |
| Linux x64 | `/usr/lib/vst3`, `/usr/local/lib/vst3`, user's `.vst3` directory |

Operators register additional roots with `CANTABILE_VST3_PATHS`, separated by the
platform path separator (`;` on Windows, `:` on Linux). Register these at host
startup; choosing/replacing instruments afterward requires no configuration edit
or restart. Tool inputs accept catalog/track/artifact IDs, not filesystem paths.
VST2, AU and AAX are explicitly unsupported, and other platforms are rejected.
Symlink/reparse-point discovery entries are skipped. Discovery is bounded to
512 modules, 32,768 visited entries, 12 directory levels and 2,048 audio classes.

Each module is listed in a separate worker. Every audio class (instrument or
effect) is then inspected in another isolated worker. A crash, invalid module or
timeout is recorded and the scan continues with other modules. Workers have a
30-second deadline, optionally shortened with operator setting
`CANTABILE_VST3_SCAN_TIMEOUT_MS` (1..30000). Completed modules persist during the
scan; cancellation retains their inspection records. An interrupted first module
does not erase the prior registry.

`instruments_list.catalog` returns names, vendors, versions, instrument/effect
classification, bounded bus metadata, inspection errors and IDs. The private
`vst3-catalog.json` ledger contains paths; paths never enter a catalog-backed
instrument reference or public catalog response. IDs derive from standard class
IDs. Duplicate installed copies of one class use the first path in ordinal sort
order. The ledger can be rebuilt from installed plugins; it is not a second
music project.

The state fields mean different things:

| Field | Meaning |
| --- | --- |
| `detected` | Module path currently exists |
| `loaded` | Most recent inspection initialized this class successfully |
| `usable` | Inspected instrument has the supported event input/main output/sample-size contract, and its module still exists |
| `last_render_audible` | `null` until a validated render has produced finite non-silent PCM; then `true` |

Audibility is an observation of a rendered sound, not a guarantee that every
library, preset or note will sound. Scan never plays probing notes and never
claims audible output merely because a module loaded. Effects remain visible
but cannot be assigned as instruments. Missing/failed classes retain explicit
unavailable records rather than switching to another plugin.

## Assign, inspect and preserve sound state

1. Scan and read `instruments_list.catalog.instruments`.
2. Call `instrument_assign {"track_id":"keys","catalog_id":"..."}` for each
   musical part. Reassigning resets that track's previous state.
3. Inspect with `vst_inspect {"track_id":"keys"}` or run a read-only
   `vst_script` with the same track selector. Results identify the track. Existing
   calls without a selector still target the legacy configured instrument.
4. To change sound, enable the existing operator opt-in
   `CANTABILE_VST3_SCRIPT_WRITE=true`, then call `vst_script_begin` with `track_id`
   and the existing bounded version-1 script. Include a `save_state` operation
   when the changed sound should be retained.
5. Poll the job, obtain the state artifact ID, then call
   `instrument_state_set {"track_id":"keys","file_id":"..."}`. It validates
   the standard preset header/class ID and copies the bytes into a native edit.
   The next asynchronous render explicitly loads these bytes into that instance.
6. Use `project_save`. Reopen restores assignments, embedded states and mixer
   settings. Releasing the source state artifact does not remove the saved sound.

Two tracks assigned the same class are separate instances, identified by their
stable native track IDs, with independent sound state. `instrument_clear` removes
the assignment and state; native Undo restores it. Inspector and Script calls
initialize an isolated worker from the selected instance state, not a shared
global live instance. Script outputs are not automatically edits to the project.

VST selection and selection of a plugin's internal library/sound are separate:
only standard published parameters, programs and state APIs are available.
Internal browsers, GUI-only selectors, Bandmate and other vendor-private features
are not inferred from an installed VST. Synchronous legacy scripts remain for
short operations; a longer script should use `vst_script_begin`.

## Render a common timeline

`audio_render {"max_seconds":600}` snapshots the native revision and returns
immediately. Poll `audio_status` (or `vst_job_status` for a project job); cancel
with `audio_cancel`. Project jobs report `completed_units` / `total_units`, then
`result` containing a mix `artifact`, `audio` measurements and per-track `stems`.
Existing legacy jobs retain their existing top-level artifact/audio response.
Use existing `file_read` to retrieve complete bytes and verify digests.

Native `audio.plan` exports each active musical part with the same global
tempo/meter events, song-origin rests and maximum end tick across all tracks,
including muted ones. Solo/mute selection is native. Each part is rendered
sequentially in its own process at 48 kHz; plugin latency is compensated by the
existing renderer and every part gets the same two-second release tail. Blocks
split at scheduled event times without shifting notes, including when an
instrument loses events at its own internal processing-slice boundary.

Temporary sources and processed stems use IEEE float32 stereo WAV, preserving
samples above full scale. The host sums in float64 and exports the mix as PCM16
stereo WAV. It applies track gain/balance before summing; saturation occurs only
at mix PCM conversion. `peak` and `clipped_samples` describe the pre-saturation
mix. Float stems include track gain/balance and share exactly the mix's origin
and frame count. There is no normalization, limiter or mastering in this path.

All workers/mix validation must succeed before artifacts are published as one
batch. Failure/cancellation releases pending outputs and returns no completed
mix; previously published artifacts remain immutable. Jobs live until host
shutdown (32 retained job records); completed files/projects survive restarts.
Running jobs are cancelled on shutdown and are not resumed automatically.

Limits: 32 active parts, 1 MiB MIDI per part / 16 MiB total snapshot, existing
128 MiB artifact and 512 MiB workspace quotas, plus a conservative 512 MiB
aggregate temporary-source/stem/mix budget. `max_seconds` includes the two-second
tail and is limited to 1..600 per part; actual allowable length can be shorter
under byte quotas. An all-muted/no-active-part project fails explicitly. Program
Change/SysEx and unmapped controllers retain the worker's explicit unsupported
errors; no event or sound is silently replaced.

## Failure and rollback summaries

`vst_script_begin` runs beyond the MCP request deadline while status/cancel and
the editor remain responsive. Script timeout still obeys its bounded DSL limits
(at most 120 seconds). Failed jobs have a typed `error` with a bounded code and
curated `rollback` / `audit` information. Worker messages, arbitrary text, paths
and extra plugin fields are discarded.

| Rollback status | Meaning |
| --- | --- |
| `not_needed` | Native worker reported no restore was needed |
| `restored` | Native restore was attempted and reported success |
| `failed` | Native restore reported failure |
| `unknown` | Worker was killed/crashed or no trustworthy result exists |

`attempted` / `restored` are nullable booleans. Hard cancellation never claims
successful restoration. Each worker is discarded afterward. Band instrument
resolution/render failures identify `track_id`. Legacy synchronous error envelopes
retain code-only compatibility; use the async job for the detailed summary.

## Verification and workstation acceptance

Deterministic native/host tests cover time maps/rests, state validation/history,
missing instruments, scan crash/timeout isolation, float cancellation before PCM
clipping, non-finite audio, async work beyond a request lifetime, cancellation
and summary scrubbing. `band_mcp_smoke.py` uses the SDK MDA instruments, generated
MIDI and temporary audio to verify three distinct classes / four instances,
different states of the same class, project reopen, aligned stems/mix, replacement
without restart, solo/pan, late failure and cancellation after a finished stem.
The Windows workflow runs this test and includes this document in the package.

Commercial acceptance is separate and remains pending: on one production host,
discover installed instruments; assign drums, bass and keys; select licensed
sounds through their available standard interfaces; save/reopen; export/listen
to stems and mix; change one instrument without restarting; test long work,
cancellation and complete-byte artifact retrieval. Record private module paths,
installed-product inventory, license/preset information, commercial audio and
raw logs outside this public repository. Do not label the deployment as ready
for band production before this gate.
