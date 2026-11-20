# CantabileClient

CantabileClient is a headless VST 3 instrument host. It reads one MIDI instrument
part with Cantabile's existing MIDI codec and renders the selected instrument to
48 kHz stereo PCM16 WAV. The standard MIDI remains editable in Cubase. You can
open the WAV and MIDI at the same position, route MIDI to the original instrument,
and continue editing notes, timing and velocity.

The development build also provides a read-only VST 3 Inspector and a restricted
JSON Script Controller. These use standard VST 3 interfaces; they do not open a
general-purpose interpreter or call private songwriting APIs. See the
[operation specification and EZ-series investigation procedure](../../docs/VST3_CONTROL.md).
In the extracted portable preview, the same specification is included as
`docs/VST3_CONTROL.md` beside the installed README.

## Updating on Windows without manually downloading ZIPs

Use [CantabileClient-Update.cmd](CantabileClient-Update.cmd), the existing
GitHub CLI update launcher. Copy it to a permanent, writable parent folder,
outside any versioned `CantabileClient-<run-id>` folder.

Install GitHub CLI first, then check authentication under your normal Windows
account in PowerShell:

```powershell
gh auth status
# If not authenticated:
gh auth login
```

Double-click `CantabileClient-Update.cmd`. It selects the latest successful
**push to main** run of `cantabile-client.yml` and downloads the
`CantabileClient-win-x64` artifact next to the launcher. PR builds and failed or
unfinished main builds are excluded. After a PR is merged, wait for the main
Windows workflow to succeed before running the launcher to obtain that change.

- Each build is extracted into `CantabileClient-<run-id>`. A `.complete` marker
  skips a repeated download after the executable has been found.
- `CantabileClient-current.txt` records the selected folder.
- If discovery/download fails (including an expired artifact), the launcher
  tries the recorded installation, or the sibling `CantabileClient` folder.
  Check the displayed folder/build ID: fallback can launch an older version.
- It opens PowerShell in the selected folder and prints `cantabile-client
  --help`. It does **not** start the MCP host or restore `$plugin`/`$data` from
  another shell. Configure those variables in the new shell when needed.

Keep MIDI, WAV, presets and private connector credentials in a separate data
folder. Switching version folders does not migrate files or running processes;
restart an existing MCP host explicitly with the new host executable and the
same intended workspace/settings. The launcher is a preview-artifact updater,
not a published-release installer.

## Windows quick start

1. Open the instrument in Cubase, select the sound, then choose **Save Preset**
   from the instrument control panel. Use that `.vstpreset` and the corresponding
   installed Windows x64 `.vst3` module. Set up and authorize the instrument
   normally under your Windows account first.
2. Drag one MIDI part from Cubase's Project window to File Explorer to export
   a `.mid` file. Leave some silence before the first note in the part if
   needed for the arrangement.
3. From the extracted Windows preview, run the following in PowerShell.
   Replace the example paths with the installed module and your two files.

```powershell
$plugin = 'C:\Program Files\Common Files\VST3\Kontakt 8.vst3'
.\bin\cantabile-client.exe list --plugin $plugin
.\bin\cantabile-client.exe --plugin $plugin --preset '.\Studio Piano.vstpreset' --midi '.\Piano.mid' --output '.\Piano.wav'
```

If a module lists several instruments, pass `--class-id` with the ID from
`list`. Without `--preset`, the worker uses the plugin's default state.
The output path must be new. A successful render prints JSON with the sample
rate, duration, peak and clipped-sample count.

### Inspect and control public VST 3 features

Inspect the same module and preset before selecting any parameter to change:

```powershell
.\bin\cantabile-client.exe inspect --plugin $plugin --preset '.\Studio Piano.vstpreset'
```

The JSON describes the selected class, public parameters, MIDI mappings, buses,
program information and a defined set of standard interfaces. An unsupported
interface is reported explicitly. An event output bus identifies a capability;
it does not establish that an internal song can be exported or triggered.

For scripts, create a private data directory and a UTF-8 JSON request using the
closed operation set in [VST3_CONTROL.md](../../docs/VST3_CONTROL.md). Then run:

```powershell
.\bin\cantabile-client.exe script --plugin $plugin --preset '.\Studio Piano.vstpreset' --script '.\request.json' --workspace '.\script-data'
# Add --allow-write only for a reviewed request that changes parameters,
# loads/saves state, queues MIDI or renders files.
```

Without `--allow-write`, scripts only inspect or read parameter values. Every
script validates the Inspector result before executing operations. File operands
are single portable basenames within the configured workspace. No loops,
expressions, OS commands, Python or JavaScript are accepted.

If a sample instrument returns `SILENT_AUDIO`, try adding
`--realtime --warmup-ms 5000`. This requests VST real-time processing,
paces blocks to the sample clock, and lets the instrument prepare for five
seconds before the first note. The startup wait adds no frames to the WAV.
Either flag can be used on its own to test processing speed or startup time.
The default CLI and MCP render path remains offline; MCP hosts can opt in using
the settings below. The startup wait pumps Windows messages; it does not process
silent audio blocks before MIDI begins.

### EZkeys 2: avoid simultaneous song-track playback

When rendering an external MIDI file, turn **Follow Host off** in EZkeys and
save a new Cubase `.vstpreset` with that setting. Otherwise MIDI stored in the
instrument's own Song Track can play along with the external part, producing
unexpected notes, note lengths or an apparent cutoff when the external part ends.
Keep the original preset if it is also used for songwriting.

On 2026-11-20, an operator confirmed sustained notes from the original Cubase
MIDI with this saved setting and `--realtime --warmup-ms 5000`, without adding
leading silence to the MIDI. This confirms that CLI configuration for one
workstation/preset; it is not acceptance of other instruments or Windows Hub
rendering. The two-second release tail can leave some silence at the end.

In Cubase, import the `.mid` as a MIDI part and assign the same instrument and
preset. For placement, align MIDI and WAV to the same bar. When working in an
existing project, Cubase can keep that project's tempo map on MIDI import. The
WAV follows the tempo in the rendered MIDI file, so use matching tempo maps
when comparing projects with tempo changes.

See Steinberg's guides for
[saving a VST preset](https://www.steinberg.help/r/cubase-pro/15.0/en/cubase_nuendo/topics/vst_instruments/vst_instruments_saving_vst_presets_t.html),
[exporting a MIDI part](https://www.steinberg.help/r/cubase-pro/15.0/en/cubase_nuendo/topics/parts_events/parts_and_events_midi_files_exporting_t.html),
and [MIDI import options](https://www.steinberg.help/r/cubase-pro/15.0/en/cubase_nuendo/topics/importing_audio_and_midi/importing_audio_and_midi_import_options_for_midi_files_r.html).

## MCP on the machine with the instrument

Use the portable package or build the existing Cantabile MCP host. Launch it
under the Windows account where the instrument and sound libraries are installed.
Configure the worker executable and a single instrument locally.

```powershell
$env:CANTABILE_CORE_LIBRARY = (Resolve-Path '.\bin\cantabile_core.dll').Path
$env:CANTABILE_RENDERER = (Resolve-Path '.\bin\cantabile-client.exe').Path
$env:CANTABILE_VST3_MODULE = $plugin
$env:CANTABILE_VST3_PRESET = (Resolve-Path '.\Studio Piano.vstpreset').Path
# For a module exposing multiple instruments, set CANTABILE_VST3_CLASS_ID.
# For EZkeys 2, use the saved Follow Host OFF preset and these optional settings:
$env:CANTABILE_VST3_REALTIME = 'true'
$env:CANTABILE_VST3_WARMUP_MS = '5000'
.\host\cantabile-host.exe stdio --workspace '.\workspace' --permission edit
```

For Hub access, provision a dedicated upstream and use the existing
[`serve` setup and catalog procedure](../../host/README.md#hub-service).
The workspace must be private to the Windows user running the host.
The Hub routes `midi_import`, `audio_render`, `audio_status`, `midi_export` and
`file_read` to that host. The configured plugin and preset are local operator
settings. The MCP interface accepts MIDI and the usual revision guard.
Local `instruments_list` reports configuration availability. A successful render
through `audio_status` verifies that a particular instrument actually played.

`CANTABILE_VST3_REALTIME` accepts `true`/`false` (case-insensitive) or `1`/`0`;
`CANTABILE_VST3_WARMUP_MS` accepts an integer from 0 to 30000. Unset/empty values
default to offline/zero wait. Invalid values stop a VST-configured host with
`invalid_configuration`. These trusted host settings apply to stdio and Hub
jobs and cannot be changed by an MCP caller. Restart the host after changing
them. FluidSynth does not use these VST settings.

The same connection exposes `vst_inspect` and `vst_script` for read-only
operations. `vst_script_write` additionally requires the host's `edit` grant,
the current revision/session guard, and trusted
`CANTABILE_VST3_SCRIPT_WRITE=true` configuration. A caller cannot enable that
configuration or select another executable/module. Script inputs bind workspace
artifact IDs to portable names, and completed outputs use the existing bounded
`file_read` transfer. See the exact [MCP request contract](../../docs/VST3_CONTROL.md#mcp-contract).

`instruments_list` and successful `audio_status.audio` report `process_mode` and
`warmup_ms`. Confirm `vst3`, `realtime` and `5000` on the Windows connection before
testing the original MIDI. The job wall-clock budget includes the startup wait
in addition to the render limit and normal safety margin. The wait adds no WAV
frames or leading silence.

For a PC connected through Hub, use a separately provisioned Windows upstream
and its private credential file; retain an existing Linux/SoundFont connection.
With the same environment settings, launch the host's `serve` command as described
in the host guide. Keep that host running under the account with the instrument
and sample licenses. Until it connects and returns a real WAV through MCP, CLI
acceptance alone does not establish remote availability.

## Build and verify

- Visual Studio 2022 with Desktop development with C++, CMake 3.25 or later,
  Python 3.12, and nlohmann_json 3.12.0 headers. The optional MCP host uses the
  .NET SDK in the repository's `global.json`.
- Obtain Steinberg's `v3.8.0_build_66` SDK with its `base`, `cmake`,
  `pluginterfaces`, and `public.sdk` submodules. Supply the SDK location with
  `-DCANTABILE_VST3_SDK=<path>`. Omitting the option builds regular Cantabile.
- Configure Windows with `-G "Visual Studio 17 2022" -A x64`. Build target
  `cantabile-client` with `--config Release`. Build `cantabile-cli` too if
  you want to edit/export Cantabile project JSON.

The Windows workflow builds the CLI and managed host, tests rendering and
MCP round trips with the SDK's mda Piano, and uploads `CantabileClient-win-x64`.
Inspector verification precedes Script Controller verification in the workflow.
The control tests exercise parameter changes, state round trips and rejected
operations independently of the existing MIDI-to-WAV regression tests.
The same native smoke test can run on Linux x64 with
`-DCANTABILE_VST3_TEST_PLUGIN=ON`, the `cantabile-vst3-test-plugin` target, and
`audio/vst3/tests/smoke.py`.

## Preview scope

The worker starts one instrument per render and records its first mono or stereo
main output. MIDI tempo and meter changes follow the sample timeline. Note events
and poly pressure are delivered as VST events. CC, channel pressure, and pitch
bend require mappings exposed by the instrument. Unmapped controllers, Program
Change, SysEx, and multiple musical tracks produce explicit errors. The worker
adds a two-second tail, compensates up to two seconds of reported plugin latency,
and caps the total render at the configured limit.

Test fixtures use the SDK's mda Piano. Each installed commercial plugin and its
preset needs its own Windows smoke test, including licenses, sound libraries and
whether the plugin can initialize without a control panel. The worker creates
no plugin editor windows. The portable package uses the normal Microsoft x64
C++ runtime. Install the Redistributable if Windows reports it missing.

Native plugins execute machine code with the worker account's OS permissions.
The closed script language limits the control surface; it is not an OS sandbox
for an untrusted VST binary. The MCP host terminates its worker on timeout or
cancellation. Caught operation failures attempt standard state restoration and
discard new outputs; termination discards the isolated worker, so no live Cubase
instance is changed. See the operation specification for the recovery boundary.
