# Third-party dependencies

Cantabile's own source uses Apache-2.0. Third-party components keep their own
licenses; this document does not relicense them or separately supplied music.

| Dependency | Use | License / source |
| --- | --- | --- |
| nlohmann/json 3.11+ | Native project/command JSON | MIT, https://github.com/nlohmann/json |
| Flamoris.Mcp.Core 1.4.0 | Shared MCP connection, grants and typed operation adapter | Apache-2.0, https://github.com/flamoris-jp/flamoris-mcp-core |
| Flamoris.Logging | Shared diagnostics, dependency of MCP Core | Apache-2.0, https://github.com/flamoris-jp/flamoris-logging |
| ModelContextProtocol SDK and .NET runtime | Official protocol implementation via shared Core | Preserve the notices shipped by the restored packages and runtime |
| FluidSynth | Separately loaded synthesis library in the audio worker | LGPL-2.1-or-later; https://github.com/FluidSynth/fluidsynth |
| SoundFont | User-configured instrument/sample data | Separate bank-specific terms; not bundled in this repository |
| Steinberg VST 3 SDK 3.8.0 build 66 | Optional CantabileClient build-time SDK and test plugin | MIT, https://github.com/steinbergmedia/vst3sdk/tree/v3.8.0_build_66 |
| mido 1.3.3 | Independent parser in development tests | MIT; test-only, not bundled with runtime |

The Docker recipe installs FluidSynth from the Ubuntu package repository and
retains distribution copyright/license files. It also copies the build-time
nlohmann/json copyright notice into the runtime image. Do not remove required
package/runtime notices when producing release artifacts.

Actual audio tests use an independently installed TimGM6mb SoundFont. No bank,
generated MIDI, or generated audio is committed. SoundFont usage/distribution and rendered-work terms must be checked for each chosen bank.
CantabileClient binaries preserve the SDK notices with the portable package.
Third-party instruments, presets, sample libraries and the SDK smoke-test plugin
are not distributed in that package.
