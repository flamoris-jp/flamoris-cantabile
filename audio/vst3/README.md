# CantabileClient

CantabileClient is a headless VST 3 instrument host. It reads one MIDI instrument
part with Cantabile's existing MIDI codec and renders the selected instrument to
48 kHz stereo PCM16 WAV. The standard MIDI remains editable in Cubase. You can
open the WAV and MIDI at the same position, route MIDI to the original instrument,
and continue editing notes, timing and velocity.

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
