# First implementation acceptance / 初期実装の検証

This records local source verification for the 0.1.0 implementation candidate.
It is not a release, deployment, or actual client-listening receipt.

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

- Final-revision GitHub CI observation, including Windows native build.
- Actual Docker build/run and Updater recipe/install/update acceptance.
- Actual Hub WSS connection, schema parity and deployment configuration.
- Actual Cubase import and visual/playback acceptance.
- VST 3 hosting, plugin preset/parameter state, range/stem rendering.
- Native MCP audio presentation and actual model audio input/listening.
- Windows GUI and cross-platform runtime/audio compatibility.

This execution environment denies local socket/named-pipe listeners, so the local
MCP integration uses the official SDK's direct stdio transport over the same Core
permission/editing boundary. No socket restriction was disabled. The actual Hub
WSS provider and loopback health listener remain separate live acceptance gates.
Docker is not available locally. Distribution metadata fixture tests do not
substitute for image/startup/Updater verification.
