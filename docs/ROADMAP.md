# Cantabile initial roadmap / 初期ロードマップ

Status: `planned`. Every implementation item below is pending.

## Accepted direction / 合意した方針

- Build a Linux-first, headless MIDI composition/export tool.
- Use Cubase as the first MIDI handoff target for instrument playback and audio production.
- Keep editable music-project data in Cantabile so later changes can target tracks, phrases, and individual notes.
- Start without a GUI.
- Add MCP access over the same editing core in a later slice, reusing applicable FLAMORIS infrastructure.

These decisions define scope, not a claim that a runtime or integration already exists.

## Phase 1 — Editable project and CLI

- [ ] Choose and document the implementation language, dependencies, project schema, and CLI contract.
- [ ] Represent tracks, phrases, notes, tempo, and time signatures.
- [ ] Preserve musical timing exactly enough to avoid cumulative rounding drift.
- [ ] Validate note pitch, velocity, duration, position, and MIDI-channel allocation.
- [ ] Save and reload the editable project.
- [ ] Edit a bounded section without regenerating the rest of a song.

Acceptance: a small multi-track music project can be created, validated, saved, reloaded, and edited from the command line without a display server.

## Phase 2 — Standard MIDI export and Cubase handoff

- [ ] Export a Standard MIDI File, initially targeting multi-track SMF Type 1.
- [ ] Preserve tempo/time-signature changes and track names.
- [ ] Handle simultaneous note events, note-off ordering, channels, and drum-track allocation explicitly.
- [ ] Export selected tracks as well as a whole song when required by the first production sample.
- [ ] Check exports with an independent MIDI parser.
- [ ] Import a sample into Cubase and record the actual application/version and observed result.

Acceptance: the imported sample has the expected note pitches, note lengths, bar positions, track structure, tempo, and meter. Instrument assignment, playback, mixing, and WAV export are performed by Cubase.

## Phase 3 — MCP access

- [ ] Inspect current FLAMORIS MCP foundations before adding transport or logging infrastructure.
- [ ] Expose bounded project inspection, edits, save/load, and MIDI export through the same core as the CLI.
- [ ] Define revision guards, editing history, file boundaries, and error responses.
- [ ] Record source verification separately from installation and live acceptance.

MCP Hub remains the routing/aggregation layer. Cantabile remains authoritative for its own project and edits.

## Later integrations / 後続候補

- Note-to-lyric associations and a verified Synthesizer V 2 handoff.
- Lightweight preview playback if it helps composition.
- Optional DAW automation, instrument rendering, or a GUI when a concrete workflow needs them.

These are candidates, not Phase 1 dependencies. Voice databases and commercial software are supplied by the user and are not bundled in this repository.

## Current checkpoint / 現在の到達点

- Repository created from the FLAMORIS template.
- Project identity, ownership boundaries, first workflow, and acceptance goals documented.
- No application code, runnable CLI, MIDI export, deployment, or live Cubase/Synthesizer V verification yet.
