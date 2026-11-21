# Cantabile roadmap / ロードマップ

Status: development — public v0.1.0 preview. Source, CI, release and bounded deployment acceptance are recorded in [ACCEPTANCE.md](ACCEPTANCE.md). Human playback is confirmed; model audio input/listening remains unverified.

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

## Acceptance progress / 検証の進捗

- [x] Observe successful release-source GitHub CI, including Windows native build.
- [x] Build/run the actual Docker image without a display/audio device.
- [x] Publish v0.1.0 preview assets with verified digests; repository is now public.
- [x] Validate the generated recipe and complete a fresh Updater installation/start.
- [x] Actual 18-tool Hub access, guarded edits/history, save/reopen, MIDI export and complete WAV retrieval.
- [x] Human playback of retrieved audio, followed by reported composition/arrangement experiments.
- [ ] Compatible update and retained-data acceptance across versions.
- [ ] Controlled process restart/recovery and live MIDI reimport.
- [ ] Import into the actual Cubase version and verify notes, tempo, meter and tracks.
- [ ] Native MCP audio presentation and actual model audio input/listening.

## Next implementation / 後続実装

- [x] Optional CantabileClient worker for offline rendering of one VST 3 instrument, explicit module/class selection, preset loading, and existing MCP job integration.
- [x] Read-only VST 3 Inspector for public parameters, MIDI mappings, buses, program information and selected standard interfaces.
- [x] Restricted JSON Script Controller for public parameter access, MIDI-to-WAV and standard preset state; separate read/write grants, limits and isolated MCP workers. See [operations and workstation gates](VST3_CONTROL.md).
- [ ] Confirm installed commercial instruments and Cubase round trips on the Windows production workstation.
- [x] Asynchronous installed-VST3 catalog, native track assignments/state, same-plugin independent instances, full-song aligned stems/mix, and detailed async script failures. See [development contract](VST3_PROJECTS.md).
- [ ] Commercial drums/bass/keys workstation acceptance for the catalog-backed project path.
- [ ] Capture actual VST 3 MIDI output events; current Inspector reports output-bus availability only.
- [ ] Investigate internal composition features separately; Issue #8 Phase 3 is outside the Inspector/Script implementation.
- [ ] Verify selected free Linux VST 3 instruments headlessly inside Docker.
- [ ] Phrase arrangement, splits/joins, track reorder/duplicate and more transforms.
- [x] Full-song aligned per-track float stems and float64-summed PCM mix in catalog-backed projects.
- [ ] Range rendering.
- [ ] Larger upload contracts as concrete workflows need them.
- [ ] Schema migration and compatible upgrade predecessor policy.
- [ ] Windows GUI using the same core, schema, typed edits and history.
- [ ] Verified Synthesizer V 2 note/lyric/phoneme handoff.
- [ ] MIDI 2.0/UMP, SMF Type 2/SMPTE and live MIDI hardware when needed.

MCP Hub routes, Updater owns deployment lifecycle, and Cantabile owns music projects, edits and its render jobs. The Windows UI will use the same editing engine.
