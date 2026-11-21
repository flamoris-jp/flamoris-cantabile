# Cantabile acceptance / 検証記録

Published-release status checked 2026-11-11 (JST): public v0.1.0 preview. Source tests, published
distribution, one deployment's live operations, human playback and model listening
are distinct evidence levels.

## Issue #8: Inspector and Script Controller local verification

The development source was built and tested locally on Linux with GCC 14.2,
Steinberg VST 3 SDK `v3.8.0_build_66` and .NET SDK 10.0.403. These checks use the
SDK's mda Piano, not EZkeys 2, EZdrummer 3 or EZbass. Windows CI for this change,
commercial-instrument controls and deployed Hub routing remain separate gates.

| Check | Observed result |
| --- | --- |
| Native CMake build | Passed |
| Managed Release build | Passed, zero warnings/errors |
| Native CTest | 1 passed |
| Native Python interoperability suite | 27 passed |
| Actual FluidSynth synthesis suite | 17 passed |
| Packaging/entrypoint suites | 10 passed |
| Existing MCP host integration | 6 passed |
| VST host safety suite | 9 passed |
| Phase 1 Inspector with mda Piano | Passed: 15 public parameters, 2 buses, 64 MIDI mappings, 8 programs |
| Phase 2 native Script Controller | Passed: parameter readback and changed processor audio, standard preset round trip, state rollback, permissions/schema/path/resource limits |
| Real MCP Inspector/Script controls | Passed: JSON inspection, parameter/state round trip, MIDI/WAV transfer, read-only/write opt-in, persistent artifacts and error isolation |
| Existing VST MCP rendering regression | Passed: offline/realtime identical PCM and MIDI, processing configuration, startup wait and cancellation |

The Inspector gate ran before Phase 2. The script tests generated temporary
MIDI/WAV/state fixtures, compared actual rendered audio after parameter changes,
and verified that a later failure restored state and published no partial
outputs. Initialization now synchronizes processor state into the edit controller
through standard `getState` / `setComponentState`, so parameter values observed
before and after a state round trip describe the same actual initial sound.
Unsupported parameter display conversion is represented by JSON `null`.

Windows workflow results are tracked in [PR #9 checks](https://github.com/flamoris-jp/flamoris-cantabile/pull/9/checks), separately from the local results above. The local MCP
checks use actual stdio/Core/native/worker calls and do not certify a deployed
Hub attachment. No commercial preset or media is committed. The
[operation specification](VST3_CONTROL.md) provides per-product
workstation steps and describes capability reports separately from MIDI-output
capture, which is not implemented in these phases.

## 2026-11-20: CantabileClient development CLI and MCP

- The workstation operator confirmed sustained notes from the original Cubase
  MIDI using EZkeys 2, a saved `.vstpreset` with Follow Host OFF, and
  `--realtime --warmup-ms 5000`. No leading silence was added to that original
  MIDI. The operator accepted a short trailing silence.
- Received WAV measurements: stereo PCM16, 48000 Hz, 754286 frames
  (15.7142917 seconds), SHA-256
  `ea797b95d79d3eac0225132b3907cc88e3c445da52aae55dc0c54b22ab8d857b`.
  These are file measurements plus operator listening, not model listening.
  No commercial preset, source MIDI or audio is committed.
- The development MCP host accepts trusted `CANTABILE_VST3_REALTIME` and
  `CANTABILE_VST3_WARMUP_MS` configuration. Default offline/zero-wait behavior
  remains. Instrument queries and completed-job audio report the selected mode
  and wait. VST startup wait is included separately in the job timeout budget.
- Local .NET 10 Release build passed with zero warnings/errors; all six existing
  real MCP host integration tests passed. The VST MCP smoke test used the SDK
  mda Piano through the actual stdio/Core/native/worker path in both default
  offline and configured realtime modes. WAV bytes were identical (307200 stereo
  PCM frames), imported/exported MIDI was exact, and the paced case took at least
  6.6 seconds with a 300 ms wait without shifting the first note.
- Five valid setting combinations and ten invalid startup configurations were
  checked. Cancellation during a 30-second startup wait terminated promptly and
  published no WAV. Caller-supplied processing options were rejected; they remain
  host-owned settings. The static Hub catalog was regenerated from the host.
- Native CLI/worker binaries used locally match the unchanged PR #7 native
  source. Windows CI and commercial-instrument MCP/Hub acceptance are distinct
  checks. At this stage the live Hub connection still reports the published
  FluidSynth backend; a separately provisioned Windows connection and a real
  EZkeys MCP render remain pending. This update does not deploy or replace it.

## Published release and CI

- [v0.1.0 preview](https://github.com/flamoris-jp/flamoris-cantabile/releases/tag/v0.1.0)
  was published on 2026-11-09; the repository is now public.
- Verified release source: `8cf8ff8082a932f8b095052bd726cc93d46a5a3d`.
- [Main CI 42616371261](https://github.com/flamoris-jp/flamoris-cantabile/actions/runs/42616371261)
  passed Linux native, Windows native, real MCP host integration and Docker checks.
- [Release run 42616608539](https://github.com/flamoris-jp/flamoris-cantabile/actions/runs/42616608539)
  passed packaged tool-catalog parity, actual offline WAV synthesis and distribution
  publication. The release includes the image archive, Updater catalog, image/source
  receipt, 18-tool Hub descriptor and license notices.
- These runs certify the release source above, not every later documentation commit.
  Windows native CI is not Windows GUI or Windows audio-runtime acceptance.

## Live deployment and use

On 2026-11-09 one Linux deployment completed a fresh Updater Docker installation,
official start/complete and independent running-container observation. Its dedicated
18-tool Hub attachment and external-client discovery succeeded.

Actual external MCP checks accepted guarded transposition, shared Undo/Redo,
explicit project save/reopen and stale-revision rejection without mutation. MIDI
export and FluidSynth WAV rendering completed; every chunk and complete-file
SHA-256 were checked independently.

| First Steps output | Observed value |
| --- | --- |
| MIDI | SMF 1, 480 PPQ, 5 tracks, 184 note-ons, 1769 bytes |
| Musical duration | 14.357136 seconds, with the composed tempo change |
| WAV | PCM16, 48 kHz stereo, 785143 frames, 3140616 bytes |
| WAV duration | 16.3571458333 seconds, including two-second tail |
| Independent PCM check | Peak 0.11151123046875; RMS 0.015440736730102383; no clipped samples |

The operator confirmed listening to the retrieved trial WAV on 2026-11-09 and
subsequently assessed the Midnight Side-Step composition. On 2026-11-10 the operator
reported using a Cantabile composition for **Happinesses**, with **Suno** providing
arrangement/vocals for an anime-opening-style MV experiment. These are production
reports, not a built-in Suno integration or Cubase compatibility test. Media and
deployment credentials/configuration are not included in this repository.

On 2026-11-11 read-only checks found the configured FluidSynth backend available
and saved composition projects listed. This is a dated observation, not continuous
health, a new render test, or restart acceptance.

## Initial local source verification

The following results were recorded during the first implementation. They are
retained as historical source evidence, not newly rerun tests for this documentation update.

## Verified locally

| Check | Result |
| --- | --- |
| Linux C++20 CMake build | Passed |
| Native codec CTest, assertions active in Release | Passed |
| Independent native Python suite, including mido | 27 tests passed, no skips (including two file-CLI boundary cases) |
| Real FluidSynth renderer suite | 17 tests passed, including actual SoundFont synthesis |
| .NET 10 MCP host build | Passed, zero warnings/errors |
| Actual MCP stdio integration | 6 tests passed |
| Updater image/catalog metadata fixtures | 6 tests passed |
| Entrypoint credential-file guards | 4 tests passed |
| Self-contained Linux host publish | Passed |
| Original eight-bar example | Project validation, MIDI export, actual stereo WAV synthesis passed |

Native checks cover general channel/meta/SysEx events, running status, timing,
overlapping notes, malformed input rollback, stable IDs, concurrency, revision,
Undo/Redo and precommit UTF-8 byte budgets. Audio checks cover tempo maps, two
sample rates, no-overwrite publication, cancellation and unsupported backends.
The SoundFont is independently installed, not bundled in source.

The MCP suite initializes the real official SDK over stdio, calls actual tools,
edits the native engine, verifies read-only permissions, saves/reopens projects,
retrieves MIDI/WAV bytes, checks SHA-256, exercises conflict/path checks, and
validates completed artifact recovery/release after restart.

The example exported a 1769-byte SMF and rendered a 48 kHz stereo PCM16 WAV:
785143 frames, about 16.357 seconds including tail, peak about 0.1115, zero clipped
samples. These measurements establish real audio creation, not musical listening
by the model. Twenty repeated small valid renders also passed after correcting
the FluidSynth worker lifetime handling.

Local commands:

```bash
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure -C Release
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s audio/tests -v
python3 -m unittest discover -s packaging -p 'test_*.py' -v
dotnet build host/Flamoris.Cantabile.Host.csproj -c Release
python3 -m unittest discover -s host/tests -v
dotnet publish host/Flamoris.Cantabile.Host.csproj -c Release -r linux-x64 --self-contained true -o artifacts/host-publish
```

Native tests need mido 1.3.3 for the independent-parser cases. Host tests need
dotnet on PATH, the built core/renderer and a separately installed SoundFont.
See the host/audio READMEs for environment variables.

## Still pending

- Compatible Updater update, migrations and retained-data behavior across versions.
- Controlled production restart/recovery and live MIDI reimport.
- Actual Cubase import and visual/playback acceptance.
- Installed commercial VST 3 Inspector/Script acceptance and state restoration;
  catalog-backed commercial drums/bass/keys acceptance and range rendering.
  The development source implements discovery, native track instances/state and
  aligned full-song stems/mix; see [the project contract](VST3_PROJECTS.md) and
  [Inspector/Script workstation procedure](VST3_CONTROL.md).
- Native MCP audio presentation and actual model audio input/listening.
- Windows GUI and cross-platform runtime/audio compatibility.

The initial local execution environment denied socket/named-pipe listeners and
had no Docker. Its official-SDK stdio checks therefore did not certify Hub WSS,
loopback health or Docker behavior. Later GitHub CI, release and bounded deployment
checks above supplied separate evidence; no local restriction was disabled.
Human playback does not establish that the AI received or heard the audio.


## Issue #10 local source verification — 2026-11-21

A Linux x64 development build used .NET SDK 10.0.403 and the existing pinned VST3
SDK v3.8.0_build_66 with its public MDA test module. No commercial preset, product
inventory, private deployment path or generated audio is committed.

Passed: 1 CTest codec case, 29 native CLI/ABI tests, 20 host/workspace/supervisor
integration tests, 17 audio-renderer tests and 10 packaging tests (77 total).
The generated 28-tool development Hub catalog matches the actual host. The six
VST smoke scripts also passed locally: Inspector, Script/state rollback, CLI
render, MIDI/WAV through MCP, control through MCP, and the new multi-instance
catalog/project/stems/mix workflow.

The new real-VST smoke covers three distinct instrument classes and four tracks,
independent states for two instances of one class, project save/restart/reopen,
correct onset rests across tempo changes, aligned float stems and PCM mix,
replacement of one instrument without restarting, solo/pan, failure in a later
part with no partial publication, and cancellation after one finished stem.
Generated synthetic-float fixtures independently verify unclipped accumulation,
pre-saturation peak/clipped-sample metering and non-finite sample rejection.
Supervisor fixtures verify isolated module crash/timeout, long async scripts
beyond the request deadline, prompt cancellation and scrubbed nullable
rollback/audit summaries.

This is local source/test-plugin evidence. GitHub Windows CI and the portable
package have their own run results. Installed commercial instruments, real
licensed library/preset choices, human playback of a commercial three-part song
and production Hub acceptance remain pending. Do not describe this as accepted
production band support until those checks have been recorded privately.
