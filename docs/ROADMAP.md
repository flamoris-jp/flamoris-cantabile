# Cantabile roadmap / ロードマップ

Status: development — first implementation candidate under review. Source completion, local tests, CI, release, deployment and client listening are separate gates.

## Implemented first slice / 最初の実装

- [x] Shared C++20 editing engine and C ABI.
- [x] CLI, project save/load, atomic edits, revision guards and Undo/Redo.
- [x] SMF Type 0/1 import/export with PPQ timing and opaque event preservation.
- [x] General channel/meta/SysEx event edits, transpose and quantize.
- [x] Shared FLAMORIS MCP adapter for workspace files, editing and bounded retrieval.
- [x] Real FluidSynth offline WAV renderer with a separately installed SoundFont.
- [x] Render snapshots, cancellation and result transfer.
- [x] Docker/Updater candidate packaging and Linux/Windows CI definitions.

Checkmarks describe source, not deployment. See [acceptance evidence](ACCEPTANCE.md) for checks actually run.

## Next acceptance gates / 次の検証

- [ ] Observe GitHub CI on the final revision, including Windows build.
- [ ] Build/run the actual Docker image without a display/audio device.
- [ ] Validate the generated recipe with the selected Updater version.
- [ ] Publish reviewed release assets with verified access and digests.
- [ ] Fresh install, compatible update, data retention and restart/recovery through Updater.
- [ ] Actual Hub connection, edits, save/load, MIDI export and complete WAV retrieval.
- [ ] Import into the actual Cubase version and verify notes, tempo, meter and tracks.
- [ ] Client audio presentation and actual model audio input/listening.

## Next implementation / 後続実装

- [ ] Native VST 3 scan/load/render, plugin parameters and preset/state persistence.
- [ ] Verify selected free Linux VST 3 instruments headlessly inside Docker.
- [ ] Phrase arrangement, splits/joins, track reorder/duplicate and more transforms.
- [ ] Range rendering and separate stems.
- [ ] Larger upload contracts as concrete workflows need them.
- [ ] Schema migration and compatible upgrade predecessor policy.
- [ ] Windows GUI using the same core, schema, typed edits and history.
- [ ] Verified Synthesizer V 2 note/lyric/phoneme handoff.
- [ ] MIDI 2.0/UMP, SMF Type 2/SMPTE and live MIDI hardware when needed.

MCP Hub routes, Updater owns deployment lifecycle, and Cantabile owns music projects, edits and its render jobs. The Windows UI will use the same editing engine.
