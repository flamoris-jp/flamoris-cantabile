> Issue #10 extends this development control path with [catalog-backed projects](VST3_PROJECTS.md).
> Inspector/scripts accept an optional `track_id` and load that instance's saved
> state. `vst_script_begin` runs the same bounded write DSL asynchronously and
> preserves typed rollback/audit summaries through job status. The legacy calls
> below remain supported. State changes become native project edits only through
> `instrument_state_set`.
> [Sound-shelf loading conditions](SOUND_SHELF.md) also apply a track's
> optional processing `preroll_ms` to targeted Script renders. The CLI uses
> `--preroll-ms`; it processes silence before musical zero and adds no WAV frames.

# VST 3 Inspector and Script Controller

This is the development contract for Issue #8, Phases 1 and 2. It extends the
existing one-instrument worker and MCP host. It does not implement internal
composition features such as Bandmate, EZkeys chord proposals or EZbass Drums &
Keys. Installed commercial instruments require separate workstation acceptance.

## Phase 1: Inspector

```powershell
$worker = (Resolve-Path '.\bin\cantabile-client.exe').Path
$plugin = 'C:\Program Files\Common Files\VST3\YOUR-INSTRUMENT.vst3'
& $worker list --plugin $plugin
& $worker inspect --plugin $plugin
# If needed, add --class-id with an ID returned by list, and --preset FILE.
```

`inspect` initializes the selected class and reads the standard public API. A
trusted initial preset can be loaded to identify its exposed state. Without a
preset, initialization synchronizes the processor's standard state into the
edit controller (`getState` → `setComponentState`) so the reported values reflect
the actual starting sound. Inspector does not issue parameter edits, MIDI or
audio processing calls. Plugin initialization/getters may themselves have vendor
implementation side effects.

The successful report has `ok: true`, `schema_version: 1`, `plugin`,
`parameters`, `buses`, `programs`, `units`, `interfaces`, `midi_mapping` and
`midi_output`. It includes these information groups:

| Group | Meaning |
| --- | --- |
| Identity | Module/class and plugin version; initial preset identity where supplied |
| Parameters | IDs, titles, units, step counts, raw flags, normalized defaults/current values and display strings |
| MIDI mappings | `IMidiMapping` assignments, indexed by event input bus, channel and controller number; channel pressure/pitch bend use the SDK's extended controller numbers |
| Buses | Audio/event inputs and outputs, names, channels, bus type and flags |
| Programs | `IUnitInfo` program lists/program names and unit information where supported |
| Interfaces | A defined set of standard interfaces queried on the component/controller |
| MIDI output | Event output bus availability, separately from actual event capture support |

Parameter fields are `id`, `title`, `short_title`, `units`, `unit_id`, `step_count`,
`flags`, `default_value`, `value` and `display`. `midi_mapping.supported`
distinguishes an absent interface from a supported interface with an empty
`assignments` list. `midi_output` reports `bus_count`, `available` and
`capture_supported` separately.

`display` is a string when the controller supplies a formatted value, or JSON
`null` when that conversion is unsupported. The Inspector does not substitute an
invented display string. Mapping `channel` values are zero-based `0..15`;
controller numbers cover CC `0..127`, channel pressure `128` and pitch bend `129`.

Interface queries cover `IComponent`, `IAudioProcessor`, `IEditController`, `IEditController2`,
`IMidiMapping`, `IUnitInfo`, `INoteExpressionController`, `IKeyswitchController`
and `IConnectionPoint`. This is an explicit inventory, not discovery of every
possible vendor extension. Unsupported optional interfaces are recorded rather
than replaced with guessed values. A zero mapped-controller count does not prove
the instrument has no MIDI behavior outside `IMidiMapping`.

An event output bus is evidence of a public MIDI-output capability. This version
reports `capture_supported: false`; it does not collect output events, export an
internal Song Track, or prove that a GUI composition action has a public API.
Program information likewise does not imply Program Change rendering support:
the existing renderer still rejects Program Change instead of guessing a sound.

Reports contain public metadata and parameter display text, not preset contents,
sample data or credentials. Before sharing a report, review plugin-supplied text
and local identifiers for personal information. Keep commercial `.vstpreset`
files private and outside Git.

## Phase 2: closed JSON operations

This is a typed operation list, not a general-purpose programming language. It
has no variables, expressions, loops, imports, network calls or OS command API.
Unknown operations/fields, invalid types, unsupported IDs and values outside the
normalized interval are errors. The configured instrument is the only target.

```json
{
  "version": 1,
  "limits": { "max_operations": 64, "timeout_ms": 30000 },
  "operations": [
    { "op": "inspect" },
    { "op": "list_parameters" }
  ]
}
```

Save the request as UTF-8 JSON and use an existing private workspace directory:

```powershell
& $worker script --plugin $plugin --script '.\read.json' --workspace '.\script-data'
# Mutating requests additionally require --allow-write.
```

Every script runs Inspector validation successfully before its operations.
The Windows workflow also runs the Phase 1 test before the Phase 2 test. Neither
a script field nor a successful build can bypass the runtime Inspector gate.

| Operation | Required fields besides `op` | Effect / permission |
| --- | --- | --- |
| `inspect` | None | Read the validated Inspector report |
| `list_parameters` | None | Read public parameters |
| `get_parameter` | `id` | Read one existing parameter |
| `set_parameter` | `id`, `value` | Write a writable public parameter; normalized finite `0..1` value |
| `midi` | `path` | Validate and queue one SMF file for the next render; write permission |
| `render` | `output`; optional `midi` | Render direct or queued MIDI to a new WAV; write permission |
| `save_state` | `name` | Save a new standard `.vstpreset` snapshot; write permission |
| `load_state` | `name` | Restore a standard `.vstpreset`; write permission |

`id` is the unsigned numeric VST parameter ID, not its array index or display
name. Choose a suitable parameter from Inspector; do not assume IDs remain
stable between plugin versions. Read-only parameters cannot be set. Parameter
changes reach the controller and are immediately flushed through the processor's
zero-sample parameter-change path; the first rendered block also receives queued
changes. Restoring state clears stale queued parameter changes.
Values are observed through the plugin API and may be quantized by stepped
parameters. No GUI controls or undocumented parameters are synthesized.

`midi` queues an existing MIDI file; it does not open a live MIDI port or play
through a sound card. `render` delivers the scheduled messages through the same
VST 3 rendering path as the existing MIDI-to-WAV CLI. Notes/poly pressure use VST
events; CC, channel pressure and pitch bend require public mappings. SysEx,
Program Change and multiple musical tracks remain explicitly unsupported.
`--realtime` and `--warmup-ms` retain their existing meanings. A script can use
the trusted initial `--preset`, then apply parameter changes before rendering.

### Limits and files

| Limit | Boundary |
| --- | --- |
| Script request | 256 KiB |
| Operations | Default 64, caller may lower or raise up to 256 |
| Total cooperative deadline | Default 30000 ms, configurable `1..120000` ms |
| File operands | One ASCII basename, at most 120 characters; alphanumeric first character, then alphanumeric, `.`, `_`, `-`; `..` forbidden |
| File kinds | MIDI inputs `.mid`; WAV outputs `.wav`; state input/output `.vstpreset` |
| New output files | At most 16; existing files are not overwritten |
| Combined new output size | At most 128 MiB across WAV and state outputs |
| State size | At most 16 MiB per worker state |
| Native response body | At most 4 MiB |
| Inspector | At most 16384 parameters, 64 buses per type/direction, 1024 units, 256 program lists and 16384 total programs |

Paths are resolved inside the selected workspace. Absolute paths, directory
components, Windows device basenames, symlinks and Windows reparse points are rejected. CLI plugin/preset
and request paths are trusted local launch settings, not script operands. The
MCP route creates a fresh private staging directory and only supplies copies of
authorized inputs. Results include an operation audit and names/kinds of new
files; no file is published as a successful result until the whole script passes.

### State and failure recovery

Writes require a standard VST preset snapshot before the first mutating
operation. If the plugin cannot provide usable state, fail before changing it.
Caught operation errors attempt to load that baseline and report
`error.rollback.attempted` / `error.rollback.restored`. Newly staged outputs are
discarded. Inspect this result: an attempted restoration is not a guarantee that
a vendor implemented state restoration correctly.

Each request owns an isolated plugin worker. It never attaches to a live Cubase
instrument or overwrites the configured initial preset. Successful changes exist
only for that worker's lifetime unless explicitly saved as a state artifact.
Rebind a saved state in a later request and call `load_state` to continue it.
The Cantabile project revision guards access to the project snapshot; it is not
a persistent VST parameter history or a Cubase automation track.

Native deadline checks cover initialization/inspection boundaries, operations
and render blocks. A plugin can block inside a native API, so a cooperative
check cannot interrupt every call. The MCP supervisor enforces a wall-clock
deadline and kills the worker process tree on timeout or request cancellation.
A killed/crashed plugin cannot perform an in-process restore; the worker is
discarded and staged results are not published. Native CLI callers should use a
supervisor when a hard deadline or crash cleanup is required.

Loading a VST loads native code with the worker account's OS permissions. The
closed DSL restricts operations; it is not an OS sandbox for an untrusted plugin.
Only install and select trusted plugins. Generic initialization errors such as
`VST_LOAD_FAILED` or a timeout cannot identify a vendor's hidden license or GUI
dialog; check authorization/sample availability locally rather than guessing.

## MCP contract

Use the existing stdio or Hub connection with trusted `CANTABILE_RENDERER`,
`CANTABILE_VST3_MODULE`, and optional `CANTABILE_VST3_PRESET` /
`CANTABILE_VST3_CLASS_ID` settings. Callers cannot replace these settings through
tool arguments. Existing `CANTABILE_VST3_REALTIME` and
`CANTABILE_VST3_WARMUP_MS` settings apply to script rendering too.

| Tool | Input | Permission |
| --- | --- | --- |
| `vst_inspect` | `{}` | Query / read-only grant |
| `vst_script` | `{ "script": DSL }` | Query; only `inspect`, `list_parameters`, `get_parameter` |
| `vst_script_write` | `{ "script": DSL, "files": { "name": "file_id" } }`; `files` optional | Edit grant, current request guard, and host `CANTABILE_VST3_SCRIPT_WRITE=true` or `1` |

The write setting defaults to false. Restart the host to change it. Supplying a
write operation to the query tool is rejected even when the caller has an edit
grant. The current MCP guard comes from `mcp.context`; use the same guard envelope
as existing guarded editing tools, not a field inside the script.

At most 16 `files` bindings map portable names to existing immutable MIDI/state
artifact IDs. Bindings cannot read arbitrary files or credentials. By default,
`input.mid` is the current project MIDI snapshot; a `files.input.mid` binding can
replace it with an exported MIDI artifact. A state output can be reused with:

```json
{
  "files": { "sound.vstpreset": "RETURNED_STATE_FILE_ID" },
  "script": {
    "version": 1,
    "operations": [
      { "op": "load_state", "name": "sound.vstpreset" },
      { "op": "render", "midi": "input.mid", "output": "take.wav" }
    ]
  }
}
```

This is one synchronous tool call, not an `audio_status` job. Request cancellation
terminates the worker; wall-clock expiration reports `vst_timeout`. Successful
native results are augmented with `artifacts` entries containing `name`, `kind`,
`file_id`, `size`, `sha256`, `mime_type` and `revision`. Retrieve complete bytes
with existing `file_read`, including its chunk/whole-file digests. Workspace
quotas still apply (128 MiB per artifact, 512 MiB total); worker state limits may
be lower. MCP worker output capture is bounded to 8 MiB; the native JSON body
limit is 4 MiB. The separate rotated
`logs/vst-audit.jsonl` contains operation summaries, not parameter values, script
payloads, preset bytes, command lines or credentials.

## EZkeys 2, EZdrummer 3 and EZbass workstation procedure

Run the following sequence separately for each installed product and preset.
Record the installed product version, selected class ID, worker build/commit,
process mode and initial preset SHA-256. Inspector/Script acceptance for these
products is pending until those observations are collected. An earlier accepted
EZkeys 2 MIDI render does not certify these new controls.

1. Authorize the instrument normally and confirm its sample libraries load in
   Cubase under the same Windows account. Save a private standard `.vstpreset`.
   For EZkeys external-MIDI tests, preserve the accepted Follow Host OFF setting.
2. Run `list`, then `inspect` with that exact class/preset. Require `ok:true` and
   review the actual parameter list, mapping support, all event/audio buses,
   program lists and interface flags. Record unsupported features explicitly.
3. Only after the Inspector test passes, choose an exposed, reversible writable
   parameter. Save its starting normalized value and display string. Use that
   actual ID in a script that saves baseline state, changes the value, reads it
   back, restores baseline state and reads it again. Do not reuse an example ID
   from another product.
4. Export one original MIDI part. Place it in the private script workspace as
   `input.mid`. Render the same MIDI with both the existing CLI and the script
   path, using the same initial preset, mode/warmup and parameter settings.
   Measure WAV format/duration/peak and listen for complete first/last notes.
   Exact byte equality is suitable only when the instrument is deterministic.
5. Test one mapped CC only if Inspector reports its assignment. Unmapped CC and
   Program Change must return explicit errors. Record event output bus presence
   separately; actual MIDI-output capture remains unsupported in this phase.
6. Run a failing script after a reversible change and inspect its rollback
   result. Confirm no new output file was published and a fresh worker still
   starts from the original private preset. Check operation/time/path limits
   using harmless inputs, without deliberately crashing commercial plugins.
7. Repeat inspection and a guarded write through MCP. Verify a read-only grant
   and a host with the write opt-in absent cannot write. Retrieve the WAV and
   state artifacts completely and verify digests. Test request cancellation and
   observe no completed artifact. Separately test the intended Hub connection;
   stdio success alone does not certify routing/deployment.

For a PowerShell request file, generate UTF-8 without BOM on Windows PowerShell:

```powershell
$request = @{ version = 1; operations = @(@{ op = 'list_parameters' }) }
$requestPath = Join-Path $PWD 'read.json'
[IO.File]::WriteAllText($requestPath, ($request | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
& $worker script --plugin $plugin --script $requestPath --workspace '.\script-data'
if ($LASTEXITCODE) { throw 'Inspector/script failed; inspect its JSON error.' }
```

Store reports/digests and a short acceptance record separately from commercial
state/audio. Do not commit WAVs or encoded media. The SDK mda test instrument can
be used in CI for deterministic control, state and rendering checks; it does not
establish any commercial product's exposed capabilities or licensing behavior.

## Automated verification

Build with the SDK's mda test instrument enabled, then run the gates in this
order. Substitute the actual worker, CLI, module and core-library paths:

```text
python audio/vst3/tests/inspector_smoke.py WORKER MODULE
python audio/vst3/tests/script_smoke.py WORKER CLI MODULE
python audio/vst3/tests/control_mcp_smoke.py WORKER CLI MODULE CORE_LIBRARY
python audio/vst3/tests/smoke.py WORKER CLI MODULE
python audio/vst3/tests/mcp_smoke.py WORKER CLI MODULE CORE_LIBRARY
```

The control tests exercise real VST public parameter values, changed audio,
standard preset round trips, closed-schema and permission failures, file/resource
limits, caught-error rollback and complete MCP artifact retrieval. Existing
smoke tests cover the original rendering entry point and existing MCP audio
workflow. All audio/state fixtures are temporary. CI evidence belongs to its
exact source commit; see [ACCEPTANCE.md](ACCEPTANCE.md) and the PR checks for
observed results. Commercial licensing, sample availability, GUI requirements
and deployed Hub routing remain workstation checks.
