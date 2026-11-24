# VST3 performance and output capture (development, Issue #15)

This adds generic output-event capture, operator-selected standard preset ingress,
and optional sound-specific performance declarations. The native project remains
the editing authority. It does not expose vendor-private browsers or composition
APIs. Product-specific library loading, licensing, sample readiness and audible
acceptance require separate workstation observations. An advertised output bus
does not prove that an instrument will output its internally generated notes.

## Capture, preview and explicitly append

All MCP calls use the existing `input` envelope and permission/revision guard.

| Tool | Input | Result |
| --- | --- | --- |
| `vst_capture_begin` | `track_id`; optional `start_tick`, `end_tick`, `max_events`, `max_seconds` | Async `vst_capture` job; source part, MIDI/time map, plugin identity, saved state and loading conditions are copied before execution |
| `vst_capture_preview` | `midi_file_id`, `report_file_id` | Query returning status, provenance, counts, conversion rules and the first 64 diagnostic events |
| `vst_capture_import` | Matching MIDI/report IDs, a **new** `track_id`, `name` | One native edit appending an editable, muted, unassigned track; Undo/Redo and save/reopen preserve its origin |

Poll and cancel with `vst_job_status` / `vst_job_cancel`. Capture does not change
the project, its revision or history, and does not re-inject generated output.
The worker processes only the selected source part, even if other tracks are
active. The default interval is musical zero through the common song end; an
explicit end must not exceed that end. Extend the native track's `end_tick` first
when a longer processing window is intended. Input may be empty for a plugin
that generates from transport; actual output still has to be observed.

Capture currently requires one event output bus and the existing supported
instrument processing contract (event input, mono/stereo main audio output,
float32 at 48 kHz). It activates the public bus and supplies a bounded `IEventList`
through `ProcessData.outputEvents`. Audio samples are discarded; silent audio
does not invalidate an event capture. Inspector reports host `capture_supported`
separately from bus count and `instance_capture_compatible`. Its
`observed_event_count` remains null because Inspector never processes audio.

Successful jobs atomically publish standard SMF Type 1 MIDI and a private
`.vstcapture` JSON report through the existing immutable artifact/digest contract.
`capture_status` is `captured` or `no_events`; empty capture is inspectable but
cannot be imported. Missing output bus, unsupported multiple buses, unsupported
events, malformed events, event/duration/quota limits, worker failure and
cancellation have distinct errors/states. Failed/cancelled jobs publish no
successful subset. Existing artifacts remain unchanged. Report and MIDI are
paired by SHA-256; pairing, release, tampering and restart use the workspace's
existing artifact checks. Reports are retained/released like other exports.

### Time, interval and MIDI 1.0 projection

The renderer's tempo/meter schedule and continuous sample clock are reused.
Absolute output position is block origin plus the SDK `sampleOffset`; offsets
must be inside the actual variable-size block. `ppqPosition` supplied by a plugin
is not used as the time authority. Tempo integration uses extended precision;
sample timestamps map to the nearest integer tick, with positive half ties up.
Input scheduling has the renderer's existing sample rounding. No audio-latency
offset is subtracted from event time. The SMF retains song-origin rests and the
same conductor tempo/meter events through the capture interval.

Silent processing preroll runs with transport stopped; its events are counted
and discarded. Processing before `start_tick` runs from song zero so stateful
patterns keep their intended origin; its output is counted and discarded.
The main interval includes the requested end sample so an exact final note-off
can be retained. A two-second stopped-transport postroll follows. Only note-offs
closing already retained channel/pitch notes are accepted in postroll; new
tail notes and other tail output are discarded/countable. Postroll note-offs can
extend the SMF track's end beyond the requested interval. No synthetic note-offs
are added. Unmatched note-offs and unterminated/overlapping channel/pitch notes
are reported explicitly, including when range filtering causes an unmatched off.

Supported events are Note On, Note Off and Poly Pressure on zero-based channels
`0..15`, note numbers `0..127`. Note names/octave labels are not used. Normalized
velocities/pressure round to nearest 7-bit values, positive half ties up; Note On
has minimum velocity 1 to avoid turning into MIDI 1.0 Note Off. Nonzero tuning,
non-finite/out-of-range values, invalid buses/offsets and all other event types
(including legacy CC output, SysEx and note expression) fail explicitly instead
of being silently reinterpreted. All emitted events count toward the bound,
including ignored preparation/tail events and unsupported types.

The report retains each accepted event's sample frame, tick, stable receive
order, type, channel, pitch, original normalized value, note ID, flags and phase.
SMF cannot represent VST note IDs or independent expression for overlapping
same-pitch voices. Raw On/Off order and overlaps are preserved; counters group by
channel/pitch, and no identity-preserving pairing is claimed. Original VST
Note On `length` does not synthesize an end: the SDK requires a following off.
The report's conversion section makes these losses explicit.

Import reuses the native SMF parser in the typed `track.import_midi` edit.
It validates PPQ and the current tempo/meter map through the captured interval,
rejects an existing destination, regenerates stable event IDs and preserves the
input tracks. A fresh guard alone cannot override a timing mismatch. Later
ordinary MIDI edits operate on the appended raw channel events. Muted/unassigned
is deliberate: choose a destination sound and unmute explicitly for playback.

The appended track stores portable provenance: format version, source track,
catalog/class/plugin version, source revision, input/state/MIDI SHA-256 and
capture interval. A missing saved state is identified as `plugin_default` in the
report (its state digest is SHA-256 of empty bytes); it is not proof of a
reproducible vendor default. Origin metadata survives report release and project
save/reopen; full per-event SDK diagnostics require retaining the report.

| Bound | Value |
| --- | --- |
| Source MIDI | Existing 1 MiB per part |
| Requested interval ticks | `0 <= start < end <= 2147483647` |
| Processing duration | `max_seconds` 1..600 including two-second tail; default 600 |
| Output events, all phases | `max_events` 1..8192; default 8192 |
| Captured SMF / report | 1 MiB / 2 MiB |
| Loading wait / silent preroll | Existing per-sound 0..30000 ms each |
| Hard worker deadline | Requested seconds + loading wait/preroll + 30 seconds |
| Jobs / exports | Existing 32 retained jobs, per-kind concurrency, artifact/workspace quotas |

CLI uses the same worker path:

```text
cantabile-client capture --plugin INSTRUMENT.vst3 --class-id CLASS_ID
  --midi ORIGINAL.mid --output GENERATED.mid --start-tick 0 --end-tick 1920
  --max-events 8192 --max-seconds 10
```

CLI stdout contains the bounded report; the new MIDI path is never overwritten.
Trusted local `--preset`, `--realtime`, `--warmup-ms`, `--preroll-ms` settings
retain their meanings. CLI callers supply their own hard process supervision.

## Standard preset ingress and internal libraries

`instrument_state_import {track_id, data_base64}` accepts one operator-selected
standard VST3 `.vstpreset` of at most 1 MiB. It validates version, class ID,
component chunk, non-overlapping bounded container entries and native project
size, then embeds the copy in one native edit. It preserves mixer/loading
conditions, records the installed plugin version and removes stale shelf and
performance identity. Container validity does not certify vendor semantics or
sample availability. The tool accepts bytes, never an arbitrary remote path.

When an internal browser has no public API:

1. Load the desired licensed instrument/library in its official GUI and save a
   standard VST3 preset through the host's supported save operation.
2. Supply that deliberately selected preset to `instrument_state_import` on an
   assigned matching VST class. Vendor-native preset formats must first use a
   legitimate vendor/DAW conversion; binary rewriting is unsupported.
3. Run targeted `vst_inspect` against the restored state. Use the actual public
   parameter IDs, mappings, program metadata and documented MIDI triggers.
4. Render/capture, save/reopen in a fresh worker and review actual settings and
   first/last notes. Register the state in the existing private sound shelf.

An anonymous automation slot is not inferred to be a library selector. Program
metadata is not a list of installed internal libraries. Program parameters can
only use the existing validated public parameter path where exposed. No private
API, DRM bypass, preset scraping, commercial phrase distribution or AI training
is added. States exceeding the existing 1 MiB/project budgets fail; they require
measured product-specific follow-up rather than an unbounded quota increase.
Wrapper load failure remains confined to its worker; other instruments continue.

## Performance declarations on a saved sound

`sound_performance_set {sound_id, expected_sha256, performance}` attaches optional
metadata to that exact retained state. `sound_search` returns it and can filter
by `control_kind`. Assignment copies the declaration into the native instrument
so save/reopen and Undo/Redo preserve it independently of later shelf changes.
Reimporting/replacing state clears stale declarations. Shelf metadata updates
are outside native history, just like existing shelf evidence updates.

Control declarations are **operator-supplied** sources/evidence, separate from
audibility and licensing. To retain an actual capture observation, optionally
supply both `capture_midi_file_id` and `capture_report_file_id` to
`sound_performance_set`. The host verifies paired artifacts, positive output and
the exact shelf state/class/plugin version before adding `capture_evidence`.
Callers cannot supply that reserved field as a declaration. Search with
`verification: "capture_observed"` finds these observations. The retained evidence
contains state/input/MIDI digests, class/version, source revision and event count;
it survives artifact release and is copied on assignment. It establishes output
for that tested input/state, not every declared control. The full report remains
the source for per-event diagnostics. Do not promote anonymous slots, GUI-only operations or untested
manual descriptions into verified capabilities.

```json
{
  "version": 1,
  "note_numbering": "midi_0_127",
  "library": "Original test instrument",
  "source": "User-selected sound; original fixture documentation",
  "requires_playing": true,
  "tempo_sync": true,
  "controls": [
    {"id":"pattern-a", "name":"Pattern A", "kind":"pattern", "notes":[36],
     "channel":0, "source":"Synthetic fixture manual", "verification":"midi_documented"},
    {"id":"transpose", "name":"Transpose", "kind":"parameter", "parameter_id":0,
     "source":"Public Inspector", "verification":"parameter_documented"}
  ]
}
```

The native validator is authoritative for both shelf and project copies. Version
1 requires `note_numbering`, `source`, `controls`; optional fields are `library`,
`requires_playing`, `tempo_sync`; `capture_evidence` is reserved for host-recorded
observations. There are at most 16 controls/16 KiB total.
Each control requires unique `id`, `name`, `kind`, `source`, `verification`.
Kinds are `trigger`, `keyswitch`, `pattern`, `cc`, `parameter`, `gui`.
The first three require 1..16 MIDI `notes`; CC requires `cc:0..127`; parameter
requires `parameter_id:0..4294967294`; `channel:0..15` is optional. Unknown fields
and inappropriate operands are rejected. Verification is `unverified`,
`gui_only`, `midi_documented`, `parameter_documented` or `operator_verified`;
the last requires explicit `evidence`. It remains an operator attestation.

Use existing `edit_batch` for the documented notes/CCs on a target part and
targeted `vst_script_begin` for actual public parameters. The existing Inspector,
normalized-value validation, write opt-in, revision guards and isolated workers
still apply. This is a small declaration contract, not a manufacturer-specific
composition engine or an executor for arbitrary instructions.

## Verification boundaries

`capture_smoke.py WORKER FIXTURE_MODULE CORE_LIBRARY MDA_MODULE` exercises actual
SDK callbacks using the repository's original `capture_fixture.cpp`. It covers
sub-block offsets/block crossing, tempo/meter changes, overlap/note IDs, pressure,
range filtering/postroll endings, empty output, unsupported/tuned/invalid output,
limits, crash/cancellation, immutable input while editing, paired artifact
retrieval/restart, native append/history/timing conflicts, changed-state ingress,
new-session recall and declarations. The existing real MDA band/shelf gates
remain separate audio regressions. Native-only tests cover append atomicity and
metadata validation. The Windows workflow runs the real capture gate before
packaging; its result must be recorded for the exact source commit.

CLI, native core, local stdio, Windows CI, deployed Hub catalog and commercial
workstation acceptance are distinct gates. The generic implementation does not
establish PLAYBOX, Session Percussionist, Discoteca Drums, Halo Vox, Kontakt,
Komplete Kontrol or EZ songwriting feature compatibility. Load the chosen
library through its legitimate workflow, inspect it, verify documented controls
against its official manual/license, and measure actual output. Use WAV rendering
or a licensed official MIDI-export route when no supported event output is
available. Record the original 130s-BPM short composition, listening, installed
inventory and commercial state/audio privately after deployment. Issue #15
remains open until those workstation gates are resolved.
