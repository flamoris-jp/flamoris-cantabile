# Cantabile roadmap / ロードマップ

Status: `planned`. Application implementation and every runtime acceptance item below are pending.

See the [design proposal](DESIGN.md) for product goals, architecture recommendations, and ownership boundaries.

## Direction / 方針

- Linux上のDockerアプリをUpdaterで導入・更新する。
- MCP Hub経由でファイル入出力、一般的なMIDI編集、音源設定、音声書き出しと取得を行う。
- C++の共通編集エンジンと既存FLAMORIS基盤を使うホスト構成を第一候補とする。
- Cubaseへの標準MIDI受け渡しを維持する。
- 将来のWindows GUIは同じ編集エンジンとプロジェクト形式を使う。

## Phase 1 — Shared editing core and project

- [ ] C++／CMake、hostとC ABI、依存関係、Linux／Windows build方針を検証・固定する。
- [ ] 曲、トラック、フレーズ、ノート、イベント、テンポ／拍子のmodelを定義する。
- [ ] 安定したID、整数tick、同時刻イベント順序、revision guardを定義する。
- [ ] 型付き編集batch、Undo/Redo、atomicな保存／再読込を実装する。
- [ ] CLIから作成・検査・編集・保存できるようにする。
- [ ] GUIに依存しないcoreとcross-platform project compatibilityをテストする。

Acceptance: display serverなしで同じ曲データを編集・保存・再読込でき、Windows向けcore利用を妨げるGUI／OS依存がないことを確認する。

## Phase 2 — MIDI breadth and MCP file/edit workflow

- [ ] Standard MIDI Fileの読み込み・書き出しと対応表を作る。
- [ ] ノート、CC、サステイン、プログラム／バンク、ピッチベンド、プレッシャーの編集を追加する。
- [ ] テンポ、拍子、調号、マーカー、歌詞・テキスト、SysExの保持／編集範囲を明示する。
- [ ] RPN／NRPN・14-bit CC・未解釈メタイベントを含む入力で保持と差異報告を検証する。
- [ ] フレーズ編集、移調、クォンタイズ、スイングなどを型付き操作として扱う。
- [ ] 同じcoreへ接続するMCP adapterを既存FLAMORIS基盤で実装する。
- [ ] 権限付きworkspace内のcreate/open/save、MIDI upload/import/export/downloadを追加する。
- [ ] サイズ上限、chunk offset、digest、途中失敗、revision conflictとUndo/Redoを検証する。
- [ ] 独立したparserとCubaseの双方で、書き出したMIDIの音符・拍位置・tempo/meter・track構造を検証する。

Acceptance: MCPでプロジェクトを取得・編集・保存し、MIDIを往復できる。未対応データを黙って削除しない。

## Phase 3 — Free instruments, VST 3, audio output and listening

- [ ] 音源registry、track割り当て、preset/state、parameterの保存／復元を定義する。
- [ ] Linux対応フリーVST 3を選び、Docker・画面なしのロードと実レンダリングを検証する。
- [ ] Surge XT等の候補についてバイナリー／追加素材／制作物の利用条件と必要依存を確認する。
- [ ] FluidSynth＋条件確認済みSoundFontの補助backendを評価する。
- [ ] snapshotに基づく非同期WAV render、範囲指定、track別出力、余韻、取消を実装する。
- [ ] 音源欠落・失敗を明示し、worker終了時もprojectを保持する。
- [ ] 短い音声previewと完成音声のMCP取得、size／duration／digestを検証する。
- [ ] クライアントでの再生と、モデルへの実音声入力／聴取を別々に実機確認する。

Acceptance: 音符を編集して音にし、取得した音を聴いて次の修正に戻れる。音声解析の数値だけを聴取の代わりとして扱わない。

## Phase 4 — Docker, Updater, Hub and live acceptance

- [ ] アプリ所有のDockerfile、image archive、release identity、Updater catalog/recipeを実装する。
- [ ] project、素材、音源state、設定、出力が更新後も保持されるmountとmigrationを検証する。
- [ ] digest、image ID、source revision、対応predecessorと復旧方法を記録する。
- [ ] 非root・画面なし・音声デバイス不要のオフラインrenderを選定音源で検証する。
- [ ] MCP Hub向け静的catalogのschema／annotationsを実際のツールと一致させる。
- [ ] process health、Hub接続、MIDI編集、音声render、取得・試聴を別々に確認する。
- [ ] fresh install、compatible update、restart/recovery、end-to-end操作の証跡を残す。

Acceptance: Updaterが管理するLinux Dockerアプリで、Hub経由のファイル入出力 → 編集 → 保存 → 音源設定 → 音声render → 全体取得／試聴が成立する。

## Phase 5 — Windows GUI

- [ ] 同じnative coreとschemaを利用するGUIの方式を選定する。
- [ ] 同じ編集コマンドとUndo/Redoを使い、人とAIで同じsessionを操作する。
- [ ] Linux／Windowsでprojectと編集結果を比較する。
- [ ] OS別音源・preset/state互換性と欠落時の表示を検証する。

## Later / 後続候補

Synthesizer V 2への音符・歌詞・発音の受け渡し、MIDI 2.0／UMP、特殊SMF、リアルタイム外部MIDI機器、追加エフェクトやDAW固有連携は、必要な範囲を別途定義して検証する。声のデータベースや商用ソフトはRepositoryに同梱しない。

## Current checkpoint / 現在の到達点

RepositoryはFLAMORISテンプレートから作成済み。現在は設計文書のみで、CLI、MIDI編集、保存、MCP、音源レンダリング、Docker配布、Updater／Hub実機接続、Windows GUIの実装はまだない。
