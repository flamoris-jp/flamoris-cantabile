# FLAMORIS Cantabile 🎹

**Headless MIDI editing and offline audio rendering with a shared C++ engine.**

画面なしでMIDIを編集し、音楽を書き出すFLAMORIS音楽制作ツールです。将来のWindows GUIも同じ編集エンジンを利用します。

**Status: development — first implementation candidate (0.1.0), under review.** No published release, deployed service, real Hub/Updater acceptance, or Windows GUI is claimed.

## What it is / 何者か

Cantabile owns its music project, native typed edits and Undo/Redo, MIDI import/export, project persistence, and offline render jobs. CLI and MCP operate the same C++20 core through a small C ABI. The .NET 10 host reuses FLAMORIS MCP Core and Logging.

曲データと編集履歴の正本はCantabileが持ちます。MCP Hubは接続集約、Updaterは配布・更新を担当します。CubaseのプロジェクトやSynthesizer Vの歌声エンジンは、それぞれのアプリが所有します。

## Implemented source / 今回の実装

| Area | First implementation |
| --- | --- |
| Native core | C++20, stable IDs, integer ticks, atomic edits, revision conflicts, Undo/Redo |
| Editing | Tracks/events, notes, CC/program/pitch/pressure, meta/lyrics/markers/SysEx; transpose and quantize |
| MIDI | SMF Type 0/1, PPQ, running status import, event ordering and opaque payload preservation |
| Files | JSON project save/load; MCP workspace operations and bounded artifact transfer with digests |
| MCP | Shared FLAMORIS permissions/guards and WSS provider; official SDK stdio; one editing authority |
| Audio | Real offline FluidSynth + separately installed SoundFont; stereo PCM16 WAV, cancellation, result retrieval |
| Distribution | Nonroot Dockerfile, Updater candidate generator, image/source/digest checks, retained data layout |

### Supported boundary / 対応範囲

Imported note-on/off messages remain raw events to preserve overlaps, unmatched notes and release values. Composed notes support pitch/duration/velocity edits. RPN/NRPN/14-bit controllers are preserved as their underlying events. See the [exact MIDI and ABI contract](docs/PROTOCOL.md).

Type 2, SMPTE timing, extended/unknown chunks and malformed input fail explicitly. History is bounded and session-local; project saves do not persist Undo history.

The first audio backend uses a configured SoundFont and MIDI bank/program events. **VST 3 hosting, plugin presets, phrase arrangement, range/stem rendering and the Windows GUI remain pending.** Unsupported instrument settings fail instead of being silently substituted.

Audio creation and complete-byte retrieval are testable independently. Native audio presentation and actual model listening require client acceptance; a WAV file alone does not establish that the AI heard it.

## Build and try / 画面なしで試す

Prerequisites: CMake 3.20+, C++20 compiler, nlohmann/json 3.11+ headers, Python 3.12 for examples/tests. The MCP host also needs the .NET SDK selected by global.json.

~~~bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --parallel 2
ctest --test-dir build --output-on-failure -C Release
~~~

If JSON headers have no CMake package, add -DJSON_INCLUDE_DIR=/path/to/include. Windows uses the same CMake project; Windows runtime acceptance remains pending.

Create an original eight-bar project in a new file, then export MIDI:

~~~bash
python3 examples/create_demo.py /tmp/first-song.cantabile.json
build/cantabile-cli validate /tmp/first-song.cantabile.json
build/cantabile-cli export-midi /tmp/first-song.cantabile.json /tmp/first-song.mid
~~~

Import the MIDI into Cubase and assign instruments. MIDI does not contain a complete arbitrary VST preset; native project/audio settings and external DAW state remain separate.

With a separately installed FluidSynth runtime and licensed SoundFont:

~~~bash
build/audio/cantabile-render --midi /tmp/first-song.mid --soundfont /path/to/bank.sf2 --output /tmp/first-song.wav
~~~

The WAV path must be new. No display server, sound card, GPU or model API is used.

## MCP / Docker / Updater

- [Native ABI, commands, MIDI scope and limits](docs/PROTOCOL.md)
- [MCP host setup and tools](host/README.md)
- [Renderer and audio tests](audio/README.md)
- [Docker and Updater packaging candidate](packaging/README.md)
- [Acceptance evidence](docs/ACCEPTANCE.md)
- [Remaining roadmap](docs/ROADMAP.md)
- [Target design](docs/DESIGN.md)
- [Dependency licensing](THIRD_PARTY.md)

The first deployment target is an Updater-managed Linux Docker application connected through MCP Hub. The catalog generator prepares digested candidates; it does not publish or deploy. Credentials, endpoints, mount locations and live acceptance remain deployment-owned.

## FLAMORISの中での位置

[Organization map](https://github.com/flamoris-jp/.github) · [AI ecosystem](https://github.com/flamoris-jp/flamoris-ai/blob/main/docs/ai-ecosystem.md) · [Repository policy](https://github.com/flamoris-jp/flamoris-commons/blob/main/docs/repository-policy.md)

[Kachinco](https://github.com/flamoris-jp/flamoris-kachinco) is the reference for a shared native engine and product-owned headless session. Cantabile owns music separately. Common connection/diagnostic infrastructure remains with [MCP Core](https://github.com/flamoris-jp/flamoris-mcp-core) and [Logging](https://github.com/flamoris-jp/flamoris-logging).

## Repository principles

- Keep the repository focused on one clear responsibility.
- Treat current code, tests, documentation, and repository configuration as the source of truth.
- Keep public documentation portable: describe product/runtime contracts without publishing private hostnames, credentials, deployment topology, or machine-specific paths.
- Prefer explicit boundaries over speculative abstractions.
- Keep secrets, credentials, tokens, and private data out of source control and logs.
- Add tests where practical and document externally visible behavior.
- Inspect existing FLAMORIS shared packages before introducing duplicate infrastructure.
- AI-assisted development is welcome; submitted changes still require human review and responsibility.

## FLAMORIS

FLAMORIS is open-source software for creative work and AI-native production.

Use it however you like.

Commercial use is welcome and does not require permission. If you'd like, we'd be happy to hear what you used FLAMORIS for. This is completely optional.

FLAMORIS software is provided as-is. We do not provide individual support or guaranteed assistance.

If you run into trouble, let your AI assistant read the repository, documentation, Issues, tests, logs, and source code and help you solve it.

If FLAMORIS helps you or you find it interesting, your support helps fund development and keeps the project growing. 🌱

<sub>Mostly GPU bills.</sub>

---

## FLAMORISについて

FLAMORISは、クリエイティブ制作とAIネイティブな制作環境のためのオープンソースソフトウェアです。

勝手に使ってください。改造しても、組み込んでも、面白いものや変なものを作ってもOKです。

商用作品や製品で使う場合も許可は不要です。もしよければ「こんなのに使ったよ」と教えてもらえるとうれしいです。もちろん強制ではありません。

FLAMORISのソフトウェアは現状のまま提供されます。個別サポートや動作保証はありません。

困ったときは、README、ドキュメント、Issue、テスト、ログ、ソースコードをあなたのAIに読ませて、自己サポートしてもらってください。

もしお役に立てたり、面白いと思っていただけたなら、開発費用をご支援いただけるとうれしいです。FLAMORISは元気になって育ちます。🌱

<sub>主にGPU代とか。</sub>

## License

Code in this repository is licensed under the [Apache License 2.0](LICENSE), unless otherwise noted.

AI models, model weights, datasets, media, and other non-code assets may use separate licenses. State their applicable licenses alongside those assets.
