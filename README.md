# FLAMORIS Cantabile 🎹

Headless MIDI composition and export for humans and AI.

画面なしで曲を組み立て、標準MIDIとしてDAWへ渡すためのFLAMORIS作曲ツールです。

## Project

**Name:** `flamoris-cantabile`

**Description:** Headless MIDI composition and export tool for DAW workflows, with planned CLI/MCP editing.

**Status:** `planned` — repository initialized; application implementation has not started.

## 🧭 Repository identity / このRepositoryは何者？

### What it is / 何者か

Cantabile is a planned Linux-first tool for editing structured music projects and exporting Standard MIDI Files. The first workflow is to create MIDI without a GUI, then import it into Cubase for instrument assignment, playback, mixing, and audio export.

最初はLinux上のCLIからメロディー・コード・ベース・ドラムなどを作り、MIDIを書き出します。Cubaseへの受け渡しを最初の制作経路とし、GUIは初期実装に含めません。

### What it owns / 主な責任範囲

- Editable music-project data: tracks, phrases, notes, tempo, meter, and optional note-to-lyric associations.
- Project validation, save/load, and deterministic MIDI export.
- A future MCP adapter that operates the same project/editing core as the CLI.

曲の編集データとMIDI書き出しの正本は、このRepositoryが所有します。内部プロジェクトを保存し、フレーズや小節単位で修正できる設計を目指します。

### What it does not own / 持たない責任

- Cubase project state, instrument libraries, VST hosting, mixing, and final audio rendering.
- Synthesizer V's singing engine, voice databases, licensing, or native editing state.
- Kachinco's video timeline or media-export engine.
- Shared MCP transport, authentication, logging, or host lifecycle already owned by FLAMORIS infrastructure.

Cubaseは演奏・音作り・ミックス・音声書き出しを担当します。歌声制作ではSynthesizer V 2などの専用ソフトへメロディーを渡す方針で、歌詞・発音の自動連携は後続の検討事項です。

### Current status / 現在の状態

This repository was created from the FLAMORIS repository template. It currently contains project documentation and the shared contribution, security, licensing, and agent instructions.

CLI, project persistence, MIDI export, MCP integration, Cubase import acceptance, and singing-synthesis handoff are **not implemented or verified yet**. No runtime, deployment, or service setup is claimed.

現在はRepository初期化・方針整理の段階です。最初の到達点は「画面なしで曲データを作り、書き出したMIDIをCubaseへ取り込めること」です。

### Where it fits / FLAMORISのどこに属する？

- [FLAMORIS organization map](https://github.com/flamoris-jp/.github)
- [FLAMORIS AI Ecosystem](https://github.com/flamoris-jp/flamoris-ai/blob/main/docs/ai-ecosystem.md)
- [FLAMORIS Repository Policy](https://github.com/flamoris-jp/flamoris-commons/blob/main/docs/repository-policy.md)
- [Kachinco](https://github.com/flamoris-jp/flamoris-kachinco): a reference for the product-owned editing core and headless workflow; Cantabile owns music composition separately.

## First workflow / 最初の制作フロー

1. Edit and save a structured music project through the CLI.
2. Export a multi-track Standard MIDI File (`.mid`).
3. Import the MIDI file into Cubase and assign instruments.
4. Play, refine, mix, and export audio in the DAW.

MIDI files carry performance and timing data; instrument sounds and rendered audio remain with the receiving application. Optional vocal-melody export can later feed Synthesizer V 2. Automatic lyric/phoneme transfer requires a separate verified integration.

## Getting started

Implementation has not started, so there are no installation, build, or execution commands yet. See the [initial roadmap](docs/ROADMAP.md) for the intended first slice and acceptance criteria.

The implementation language, dependencies, project-file extension, and exact CLI/MCP commands will be selected and documented with the first implementation.

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
