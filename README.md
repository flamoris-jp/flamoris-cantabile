# FLAMORIS Cantabile 🎹

Headless MIDI editing and audio rendering for humans and AI.

画面なしでMIDIを編集し、音源を設定して音楽を書き出す、FLAMORISの音楽制作エンジンの構想です。

## Project

**Name:** `flamoris-cantabile`

**Status:** `planned` — repository and design documentation only; implementation has not started.

## 🧭 Repository identity / このRepositoryは何者？

### What it is / 何者か

Cantabile is a planned Linux-first, headless MIDI editor and audio renderer, operated through CLI/MCP and deployed as an Updater-managed Docker application. A future Windows GUI will use the same editing engine.

LinuxのDocker上で動作し、MCP Hub経由でAIが曲を読み込み、編集し、MIDIや試聴用音声を書き出す道具を目指します。共通エンジンはC++、CLI/MCPなどのホストは既存のFLAMORIS基盤を利用する構成を提案しています。Windows GUIは将来の入口です。

### What it owns / 主な責任範囲

- Editable music projects, event/phrase/track edits, revision control, and Undo/Redo.
- Project and MIDI import/export, workspace-bound file transfers, and persistent project state.
- Track instruments, presets/parameters, audio-render jobs, and retrieval of rendered audio.
- One editing core shared by CLI/MCP and the future Windows GUI.

一般的なMIDI編集を対象とし、ノートだけでなくCC、ピッチベンド、音色変更、テンポ・拍子、歌詞・マーカーなども扱う方針です。初期の対象はMIDI 1.0／Standard MIDI Fileで、個々の対応範囲と未対応データの扱いを明示します。

### What it does not own / 持たない責任

- Cubase's native project or editor state.
- Synthesizer V's singing engine, voice databases, licensing, or native editing state.
- Kachinco's video timeline.
- Shared MCP routing, transport/authorization, logging, or application-update infrastructure already owned by FLAMORIS repositories.

Cantabileは自身のMIDI編集と音源レンダリングを所有します。CubaseはMIDIを受け取る外部DAWで、Synthesizer V 2との歌詞・発音連携は別の検証対象です。

### Current status / 現在の状態

Created from the FLAMORIS repository template. Current contents are project/design documentation and the inherited contribution, security, licensing, and agent policies.

The CLI, native engine, project persistence, MIDI editing/import/export, MCP integration, VST hosting, audio rendering/retrieval, Docker payload, Updater catalog, and Windows GUI are **not implemented or verified**.

The [design proposal](docs/DESIGN.md) separates requested product goals from recommended technology choices and pending acceptance tests.

### Where it fits / FLAMORISのどこに属する？

- [FLAMORIS organization map](https://github.com/flamoris-jp/.github)
- [FLAMORIS AI Ecosystem](https://github.com/flamoris-jp/flamoris-ai/blob/main/docs/ai-ecosystem.md)
- [FLAMORIS Repository Policy](https://github.com/flamoris-jp/flamoris-commons/blob/main/docs/repository-policy.md)
- [Kachinco](https://github.com/flamoris-jp/flamoris-kachinco): reference for a product-owned native editing core, CLI/MCP, and application-owned Docker/Updater packaging.
- [MCP Core](https://github.com/flamoris-jp/flamoris-mcp-core) and [Logging](https://github.com/flamoris-jp/flamoris-logging): shared infrastructure to reuse where applicable.

## Planned production workflow / 目指す制作フロー

1. Create, upload, open, and save a project or import MIDI through CLI/MCP.
2. Inspect and edit tracks, phrases, notes, and other MIDI events, with Undo/Redo.
3. Select compatible instruments and save their presets/parameters in the Cantabile project.
4. Export MIDI for Cubase and render audio locally for listening.
5. Retrieve a bounded preview or complete audio file through MCP, listen, and revise.
6. Later, open the same project format through a Windows GUI using the shared editing core.

MIDI alone does not carry an arbitrary VST's complete preset/state. Cantabile must retain instrument settings in its own project and report what can be transferred to an external DAW. Linux and Windows instrument binaries are platform-specific.

## Getting started

There is no runnable application or installation command yet. The initial repository remains `planned`.

- [Design proposal and scope](docs/DESIGN.md)
- [Implementation and acceptance roadmap](docs/ROADMAP.md)

The first implementation should establish a shared native core and verify an end-to-end MCP editing/audio workflow. Exact commands, schemas, dependency versions, deployment settings, and supported plugins will be recorded when implemented and tested.

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
