# Cantabile acceptance / 検証記録

Status checked 2026-11-11 (JST): public v0.1.0 preview. Source tests, published
distribution, one deployment's live operations, human playback and model listening
are distinct evidence levels.

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
- VST 3 hosting, plugin preset/parameter state, range/stem rendering.
- Native MCP audio presentation and actual model audio input/listening.
- Windows GUI and cross-platform runtime/audio compatibility.

The initial local execution environment denied socket/named-pipe listeners and
had no Docker. Its official-SDK stdio checks therefore did not certify Hub WSS,
loopback health or Docker behavior. Later GitHub CI, release and bounded deployment
checks above supplied separate evidence; no local restriction was disabled.
Human playback does not establish that the AI received or heard the audio.
