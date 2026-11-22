# Private sound shelf (development, Issue #12)

The host retains individual standard VST3 states independently of temporary
exports. The native project remains the sole authority for applied track state,
loading conditions, mixer settings and Undo/Redo. The shelf is not a second
project editor. This contract is separate from the published preview and from
commercial-instrument workstation acceptance.

## Tools

All inputs use the usual `input` envelope. Transactions and mutations require
an Edit grant and the current `mcp.context` guard. Only `sound_search` is a Query.
No tool accepts an arbitrary local path.

| Tool | Required input | Behavior |
| --- | --- | --- |
| `sound_register` | `track_id`, `display_name`, `category`, `classification_source` | Copy the assigned track state or optional `file_id` state artifact into durable private storage; return a generated stable sound ID |
| `sound_search` | none | Filter by `category`, `role`, `tag`, `verification`; optional `offset` and `limit` |
| `sound_assign` | `sound_id`, `track_id` | Validate installed class/version and retained state, then apply one native track update; preserves track gain/pan/channel |
| `sound_preview` | `sound_id`, `phrase` | Snapshot and render original fixed `melody`, `bass` or `drums` notes in an isolated background job |
| `sound_verify` | `sound_id`, `initial_audible`, `restored_audible`, `human_reviewed`, `evidence` | Store dated, explicit **operator attestations**, separate from state capture and plugin discovery |
| `sound_remove` | `sound_id`, `expected_sha256` | Delete a shelf copy using its state digest; never delete project copies |

Registration optionally accepts `roles`/`tags` (at most 16 labels of 64 characters
each), `warmup_ms` and `preroll_ms` (each `0..30000`, default 0), and `realtime`
(default false).
Classification source is required. Do not label unreviewed sounds with guessed
timbre tags. Plugin class, catalog ID and version come from the host registry,
not caller-supplied identity fields. `file_id` must be a retained state artifact
and must match that assigned class. Registration does not run plugin code.

Search results expose metadata, a logical state reference, size and SHA-256,
availability and loading conditions. They never expose module paths, a state
payload or a filesystem state path. The `verification` filter accepts
`initial_audible`, `state_captured`, `restored_audible`, `human_reviewed`, and
`unverified` (restoration not yet attested). Actual installation availability
and operator restoration evidence are distinct.

`sound_preview` returns the existing job envelope. Poll `audio_status` or
`vst_job_status`; cancel with `audio_cancel` or `vst_job_cancel`. Completed
`result` has `sound_id`, `state_sha256` and `render`, whose mix/stem artifacts
use the existing `file_read` size/digest contract. Preview uses independent
native MIDI generation and isolated plugin workers; it never loads over the
current editor project or changes its revision/history. The gain is 0.5 and
notes occupy two seconds, followed by the renderer's two-second tail.
Jobs use the existing per-kind concurrency and 32-job retention bound. Preview
must not be interpreted as human listening or as changed-state restoration
proof. Voice synthesis requires the dedicated voice editor.

## Persistence and limits

The workspace owns a private `sounds` directory. Each generated sound ID maps
to one atomically written JSON record containing metadata and copied state;
on POSIX the directory is 0700 and new records are 0600. On Windows inherit the
operator's private workspace ACL. No state is added to source control.

| Limit | Value |
| --- | --- |
| Sounds | 128 per workspace |
| Standard state | 1 MiB per sound/track |
| Retained state total | 64 MiB, separate from export quota |
| Record size | 1,500,000 bytes including base64/metadata |
| Search page | default 16, maximum 32 |
| Native project | existing 2 MiB JSON budget, including embedded state |

There is no expiration or automatic eviction. `file_release` affects only an
export, not a registered copy. `sound_remove` immediately releases its shelf
storage; already-started previews retain an immutable in-memory snapshot and
projects retain independent embedded states. Shelf registration, verification
and deletion do not belong to native project Undo/Redo. `sound_assign` does.
Back up the private workspace to preserve the shelf between installations.

On assignment the native instrument contains `sound_id`, `plugin_version`,
`warmup_ms`, `preroll_ms` and `realtime` alongside `backend`, `catalog_id`, `state_base64`.
These additive fields survive save/reopen and Undo/Redo. Removing the shelf
entry does not invalidate the saved project. Newly setting a state through
`instrument_state_set` removes the previous shelf identity while preserving
loading settings. Plain `instrument_assign` resets the old instrument state.
Older native engines reject the new fields explicitly: update core and host
together. Old projects without these fields retain host-wide render settings.

Per-sound `warmup_ms` remains a startup wait before processing. `preroll_ms`
additionally processes silent blocks in real elapsed time before musical frame
zero. Transport is stopped, no notes are synthesized, and preroll audio is
discarded. Continuous sample time stays monotonic; project sample/music time
starts at zero for playback. No silent frames are added to the exported file.
This also applies to targeted Script renders. Validate the required duration,
actual initial notes, sample readiness and tails on the target workstation;
neither an idle wait nor a preset load alone proves sampler readiness. Old
shelf entries/projects without `preroll_ms` default to zero. Worker wall-clock
budgets include both startup wait and processing preroll; Script's existing
total deadline still includes all operations. CLI workers expose the same
bounded `--preroll-ms 0..30000` option. Cancellation checks run between blocks
and during paced waits, with existing supervisor cleanup and publication rules.

## Failures and evidence

State containers are checked for version, class, bounded non-overlapping chunk
entries and component state, and retained bytes are checked against their
size/digest on reuse. Plugin-semantic validity and external sample availability
remain worker checks; a structurally valid container does not prove that a
plugin can restore it. Missing samples may appear as explicit worker load or
silent-audio failures; the host cannot infer vendor-specific diagnosis.

Missing/unusable plugins, class mismatch, version mismatch, malformed/changed
state and quota overflow fail explicitly. Search reports unavailable/corrupt
entries; assignment never silently picks a different sound. No partially
applied instrument is committed on failure, including native project budget
failure. Preview cancellation/failure publishes no subset of its artifacts.

For acceptance, change an actual public parameter, save and restore that
changed state in a fresh worker/host, compare settings and audio, and keep
human listening separate. Exact audio equality is appropriate for deterministic
test instruments; commercial non-deterministic instruments need timing,
level and parameter evidence plus listening.

`host/tests/test_sound_shelf.py` checks authority, guards, permissions, temporary
artifact release, restart, different states of one class, independent mixer
settings, Undo/Redo, corruption/version failures and cancellation. The real SDK
MDA test `audio/vst3/tests/sound_shelf_smoke.py WORKER MODULE CORE_LIBRARY`
checks changed-state audio, independent instances and fresh-session recall;
the Windows workflow runs it before packaging. It does not establish commercial
instrument or vocal-engine acceptance.
