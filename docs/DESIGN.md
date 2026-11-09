# Cantabile design proposal / 設計案

This document describes the target design. See [README](../README.md) and [acceptance evidence](ACCEPTANCE.md) for the first implementation candidate. VST 3 hosting, Windows GUI, deployment and client listening remain unfinished gates.

## 1. Product goals / 目指す形

- Linux上で画面なしで動作するDockerアプリを作る。
- インストールと更新はFLAMORIS Updaterが担当する。
- 外部AIからはMCP Hub経由で接続する。
- MCPでプロジェクトとMIDIの読み込み・保存・受け渡しを行う。
- 一般的なMIDI編集をMCPから行える。
- トラックごとに対応する音源とプリセット／パラメーターを設定する。
- Linuxではフリー音源を使い、音声ファイルを書き出す。
- 書き出した音声をMCPで取得し、AIと人が聴いて修正できることを受け入れ条件にする。
- 将来のWindows GUIも同じ編集エンジンとプロジェクト形式を使う。

初期実装はGUIを持たない。CubaseへのMIDI受け渡しを維持しながら、Cantabile自身にも音源設定とオフライン音声出力を持たせる。

## 2. Recommended architecture / 推奨構成

| Component | Proposed responsibility |
| --- | --- |
| Native editing core | C++による曲・イベント・トラック・フレーズ、編集コマンド、履歴、プロジェクト保存、MIDI入出力 |
| Host / adapters | CLIとMCP、許可されたファイル操作、レンダリングJobの管理 |
| Rendering worker | 音源ロード、MIDI／パラメーター処理、オフライン音声生成 |
| Future Windows GUI | 共通エンジンの状態表示と同じ編集コマンドの呼び出し |
| MCP Hub / Updater | 既存の接続集約／配布更新基盤。曲データや編集履歴の正本は持たない |

**C++を共通エンジンに使う案を推奨する。** MIDIの時間処理、VST 3のC++インターフェース、Linux／Windowsでの共有、将来のGUIからの利用をまとめやすい。CMakeとクロスプラットフォームCIを設計に含める。C++標準と依存バージョンは初回実装で固定する。

既存の `Flamoris.Mcp.Core` と `Flamoris.Logging` は.NET 10の基盤なので、.NETホストから小さなC ABIを通じてC++コアを呼ぶ構成を第一候補とする。MCPをC++で作り直すことを前提にしない。Linuxで利用するパッケージ範囲、ランタイム、ABI、配布方法は実装時に検証する。

コアはGUI、MCP、Docker、特定ホスト名に依存させない。C++のSTL型・例外・メモリー所有権をそのまま公開ABIに流さない。ホストは新しい第二の曲モデルやUndo/Redoを作らず、同じ権威ある編集セッションへ操作を渡す。

音源のロード／実行をworkerへ分離し、音源の終了や失敗が起きても、保存済みプロジェクトと編集セッションを保持できる構成を検討する。

## 3. Music project and general MIDI editing / 曲データと編集範囲

最初の互換性基準はMIDI 1.0とStandard MIDI File。MIDI 2.0／UMP、SMF Type 2、特殊な時間基準などは対応可否を個別に記録し、未対応の入力を黙って別の意味へ変換しない。

| Area | Target operations / data |
| --- | --- |
| Tracks / phrases | 作成、削除、複製、名前、順序、ミュート／ソロ、チャンネル、フレーズ配置・分割・結合 |
| Notes | 音高、開始位置、長さ、ベロシティ、リリース値、追加・削除・移動・複製 |
| Editing transforms | 選択範囲、移調、クォンタイズ、スイング、レガート、長さ／強さの調整 |
| Channel events | CC、サステイン、バンク／プログラム変更、ピッチベンド、チャンネル／ポリフォニックプレッシャー |
| Detailed controllers | RPN／NRPNや14-bit CCなどのイベント列と、可能な範囲の意味付き編集 |
| Song metadata | テンポマップ、拍子、調号、トラック名、歌詞、マーカー、テキスト |
| Extended / opaque data | SysEx、未解釈のメタイベントや拡張データの保持・検査・明示的な対応報告 |

ノートは使いやすい編集単位として扱いつつ、元のイベント情報も保持する。未対応イベントの破棄を既定にせず、入力と出力の差異を報告する。対応を確認していない項目を「MIDI全般対応済み」と表示しない。

時間の正本は整数tickと明示した分解能／テンポマップに置き、繰り返し変換によるドリフトを避ける。イベントの同時刻順序、ノート終端、チャンネル割り当て、テンポ変更と拍子変更の境界をテストする。

各編集は型付きコマンドとして検証し、複数操作をまとめた変更はatomicに扱う。revisionの不一致を検出し、Undo/Redo、保存・再読込後の整合性、レンダリング対象revisionを明示する。

プロジェクトは標準MIDIと別の編集用保存形式を持つ。安定したID、構造化したフレーズ、歌詞と音符の対応、音源状態など、MIDIだけで表現できない情報を保持する。形式名・拡張子・schemaは未確定。

## 4. MCP file access and editing / ファイル入出力と編集

- プロジェクト一覧・作成・選択・読み込み・保存・別名保存。
- MIDIのアップロード／インポートとエクスポート／ダウンロード。
- 曲、トラック、範囲、イベント、音源設定、revision、履歴の取得。
- 型付きの編集batchとUndo/Redo。
- 音源カタログ・プリセット・パラメーターの取得と設定。
- レンダリングの開始・進捗・取消・結果取得、試聴用の範囲指定。

ファイル操作は設定されたworkspaceと権限の範囲で行う。MCPのファイルIDは任意のホストファイルや任意コード実行を許可するものではない。ファイル転送はサイズ上限、offset、digest、完了検証を持ち、不完全なアップロードを既存ファイルとして公開しない。

保存はatomicに行い、開いているプロジェクトの排他、他者の更新、破損した入力を明示する。MCPは既存の編集セッションを操作し、プロジェクト／編集履歴の正本を別に作らない。

Hubのカタログと実際のツールschema・annotationsを一致させる。秘密情報は配布物やカタログへ含めず、既存の接続・認証基盤を利用する。ツール名と上限値は実装時に固定する。

## 5. Instruments and rendering / 音源設定と音声出力

### VST 3

最初のVST対象はVST 3を提案する。トラックへの音源割り当て、class ID、バージョン、プリセット／state、パラメーター、音源リソース参照を保存する。

LinuxではLinux向けの音源を利用する。Windows向けDLLをLinuxへそのまま読み込む前提にはしない。同じ音源名でも、OS間のpreset/state互換性は個別に検証する。利用できない音源は欠落として報告し、黙って別音源へ置き換えない。

フリーVST 3音源の初期候補はSurge XT。Linux版の存在と制作物利用の条件は公式に確認できるが、CantabileのDocker・画面なしホストでの動作は**未検証**。音源選定では必要なライブラリー、CPU、プリセット／追加素材、配布条件を確認する。

### Optional SoundFont backend

一般的な楽器編成の試聴用には、FluidSynthと利用条件を確認したSoundFontのバックエンドも候補にする。FluidSynthにはオーディオデバイスを作らないファイルレンダラーがある。これはVSTではなく、VST 3対応と別のバックエンドとして扱う。

### Render contract

- 初期の基準出力はWAV。sample rate、チャンネル、sample format、レンダリング範囲、末尾の余韻を明示する。
- 曲全体、選択した範囲、トラック別の出力を設計対象にする。
- オフラインレンダリングを先に実装し、サウンドカードやGUIセッションを必須にしない。
- レンダリングは曲revisionと音源状態のsnapshotから行い、処理中の編集と混ざらないようにする。
- Jobの進捗・取消・失敗、出力のduration／size／digestを取得できるようにする。
- 同じMIDIの書き出し再現性と、乱数等を持つ音源のPCM再現性は別に評価する。
- ピーク、クリッピング、音量などの解析は補助情報として返す。

MIDI単体には任意VSTの完全なstateを入れられない。CubaseへのMIDI受け渡しと、Cantabileプロジェクトの音源設定保持は別の契約とする。VST presetやsidecarによる補助的な受け渡しは、実際に読み込める形式を検証してから案内する。

## 6. Audio retrieval and listening / 音を受け取って聴く

「音声ファイルがサーバーにできた」だけでは完成としない。

1. レンダリングした音声をMCPで取得する。
2. 短い試聴用音声をクライアントで再生可能な形で提示する。
3. 全体音声を欠損なく取得できることをdigestとdurationで確認する。
4. AI側にも実際の音声が入力され、聴取して修正判断できるかを利用クライアントで実機確認する。

MCP-native audioの再生表示、ファイルのダウンロード、モデルへの音声入力は別の受け入れ項目である。接続先クライアントが未対応の場合はその点を明示し、波形・音量・スペクトル解析だけで「AIが音を聴いた」と扱わない。既存のHubは結果を中継し、クライアント側の音声入力能力までは保証しない。

## 7. Docker, Updater, and Hub / 配布・運用

CantabileがDocker imageとimmutableなimage archive、バージョン付きrelease、Updater recipe/catalogを所有する。Updaterは導入・更新・保持データ・起動／process healthを管理する。既存Kachinco CLIの所有境界を参考にし、具体的なrecipeは現在のUpdater契約で検証する。

プロジェクト、アップロード素材、音源state、ユーザー設定、完成出力はアプリ更新後も保持する。追加音源／SoundFontの配置、実行バイナリー、永続データを分け、配布物のdigest・image ID・source revisionを記録する。

最初のimageは非rootで起動し、ホストGUIや音声デバイスの公開を前提にしない。音源の必要リソースを含めたオフライン動作は選定したbackendで検証する。接続先、credential、mount先、portなどのdeployment設定は実装／導入時に決め、架空の値を実在環境の設定として記載しない。

Updaterのprocess health、Hubでの接続、MIDI編集、音源ロード、実音声の取得は別々に確認する。公開releaseや使えるcatalogが存在することは、ソースやこの設計書だけでは証明しない。

## 8. Windows GUI / 将来のWindows版

Windows版はLinux版と同じドメインモデル、編集コマンド、Undo/Redo、プロジェクトschema、MIDI入出力を使用する。GUIフレームワークは未選定で、C++のコアをGUIから利用できる境界を先に固める。

Linux／Windowsで同じプロジェクトと編集列の結果を比較する。音源バイナリー、OS固有パス、デバイス、音源stateの互換性は別のadapter／検証対象とする。GUIのためにもう一つのMIDI編集エンジンを実装しない。

## References / 参照

- [Kachinco Linux CLI and Updater packaging](https://github.com/flamoris-jp/flamoris-kachinco/blob/main/docs/linux-cli.md)
- [FLAMORIS MCP Core](https://github.com/flamoris-jp/flamoris-mcp-core)
- [FLAMORIS Logging](https://github.com/flamoris-jp/flamoris-logging)
- [MCP Hub transport and media boundaries](https://github.com/flamoris-jp/flamoris-mcp-hub)
- [MIDI Association: Standard MIDI Files](https://midi.org/standard-midi-files)
- [Steinberg: VST 3 SDK, C++ interfaces and supported platforms](https://steinbergmedia.github.io/vst3_dev_portal/pages/What%2Bis%2Bthe%2BVST%2B3%2BSDK/Index.html)
- [Surge XT downloads](https://surge-synthesizer.github.io/downloads/)
- [Surge XT FAQ: use of generated music and plugin distribution](https://surge-synthesizer.github.io/faq/)
- [FluidSynth offline file renderer](https://www.fluidsynth.org/api/FileRenderer.html)
