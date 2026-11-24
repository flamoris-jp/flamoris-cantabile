# Kontakt sound preparation (experimental, Issue #15)

This Windows-only adapter is a first implementation toward selecting a registered
Kontakt instrument without manually exporting every sound. It uses official
Kontakt Lua loading through the public VST3 editor. It is **not yet commercial
Kontakt/PLAYBOX acceptance**. The original synthetic SDK editor tests the
host/editor/dialog/state handoff, not the vendor Lua engine, Developer setup,
licensed library compatibility, sample readiness or audio.

## Ownership and workflow

`kontakt_library_list` lists operator-registered library metadata. With a
`library_id`, it returns paginated `.nki` instrument and `.nksn` snapshot names
and opaque content IDs. Absolute paths and contents stay local. It never scans
unregistered locations, decodes a patch, copies samples or bulk-registers presets.
An instrument and a compatible snapshot must be deliberately selected together;
filenames do not prove pairing, licensing or sample availability.

`kontakt_prepare_begin` takes `catalog_id`, `library_id`, `instrument_id` and an
optional `snapshot_id`. It resolves the selection under the normal edit permission
and revision guard, then starts the existing asynchronous job contract. Poll and
cancel with `vst_job_status` / `vst_job_cancel`. Selection IDs/paths are copied at
start; installed content is still externally owned, not an immutable byte snapshot.

The worker creates a **fresh visible plugin editor**, forwards Kontakt's documented
Run Lua shortcut through `IPlugView`, and fills only that editor's standard Open
dialog. Fixed generated Lua removes default instruments **in this new instance
only**, calls `Kontakt.load_instrument`, optionally `Kontakt.load_snapshot`, and
assigns MIDI channel 1. A per-job receipt confirms that this sequence returned
successfully. The host then requests standard VST3 state with
`PresetFile::savePreset`; no vendor binary rewriting or assumed `.nkm` conversion.

The result is one immutable `.vstpreset` artifact (existing 1 MiB bound), selected
IDs and source revision. It reports `audible_verified:false` and
`project_changed:false`. Preparation never edits the current project or shelf.
Assign the matching catalog class, use `instrument_state_set`, render original
MIDI, then use existing `sound_register`, `sound_preview` and `sound_assign` tools.
The native project remains authoritative. Apply/save/Undo/Redo use existing edits;
applying after intervening edits requires a fresh revision guard.

The adapter owns no persistent standalone session, secondary project model, MIDI
loopback, phrase-generation API or commercial phrase collection. Audio playback,
MIDI output and internal generation remain separate capabilities.

## Local setup

1. Use a separately installed, licensed Kontakt 7/8 plugin and content. The
   adapter does not install/activate software or change Native Access settings.
2. Enable **Developer features** in Kontakt's Options > Developer. This is an
   explicit local prerequisite; the adapter never toggles it automatically.
3. Set `CANTABILE_KONTAKT_LIBRARIES` to a private JSON file:

```json
{
  "version": 1,
  "libraries": [
    {"id":"original","name":"Original instrument","root":"D:\\MusicLibraries\\Original"}
  ]
}
```

4. Set `CANTABILE_KONTAKT_PREPARE=true` in the host environment. This opts in to
   this fixed operation separately from the JSON Script Controller. Select a
   content directory, not a drive root. Setup is once per library root;
   individual sounds can then be selected by ID.
   The packaged `Register-KontaktLibrary.ps1` helper can create/extend this file
   with a folder picker (`-Configuration FILE -LibraryId playbox -DisplayName PLAYBOX`).
   For this explicit library it uses an existing installer `ContentDir` value
   only as a read-only folder suggestion for confirmation, never as proof of
   licensing or compatibility. It does not modify registry/installer settings.
5. Run Cantabile in an interactive Windows user session. A visible temporary
   Kontakt window appears during preparation. Avoid interacting with it while
   loading; closing it declines the operation.

No credentials, installed-library paths, commercial presets, audio or workstation
inventory belong in this public repository. The sample above is fictitious.

## Bounds and failures

- At most 32 configured roots; 64 KiB closed versioned JSON configuration.
- At most 32,768 directory entries visited, depth 12 and 2,048 recognized files
  per selected library; pages contain 1..64 entries. Discovery never reads sample
  contents. Symlinks/reparse points are not followed; linked roots/ancestors and
  missing/unreadable roots fail explicitly.
- MCP accepts typed registered IDs only. No caller Lua, shell, remote filesystem
  paths, general GUI actions or inferred automation-slot meanings.
- One preparation job at a time. It operates only the worker-owned editor and
  a standard Open dialog with matching process, UI thread and root owner. It
  requires the documented `CDM_GETFOLDERPATH` query or the complete recognized
  native filename/type/browser/Open/Cancel structure, plus a known filename edit
  control, before setting text. No desktop keyboard/mouse, focus-stealing API, clipboard,
  existing DAW/standalone or license dialog automation.
- UTF-8 paths become fixed-width decimal Lua escapes. JSON/shell quoting is never
  substituted for Lua quoting. Completion receipts are per-job and atomic.
- 120-second worker deadline, 125-second host supervision. Failure, cancellation,
  timeout, UI mismatch, missing Developer features, license/error dialogs, failed
  Lua or invalid/oversized state publish no successful artifact or project/shelf
  edit. Existing supervision kills the worker and descendants.
- This is crash/process isolation, **not an OS security sandbox**. Kontakt and
  the fixed Lua execute as the user and load sample dependencies via the engine.
- Windows only for editor preparation. Ordinary Linux headless editing/rendering
  and shelves remain available; metadata queries do not start a plugin. Other
  plugin products are rejected by the adapter.

The official API documents GUI/command-line execution, but no standard external
VST3 RPC for Lua. The editor bridge is explicit and experimental. Versions with
another dialog or without F11 handling through `IPlugView` may fail with
`KONTAKT_LUA_UNAVAILABLE` / `vst_timeout`; that is not a successful load. The direct
CLI must also be supervised because a vendor modal UI can block its calling thread.

## Verification and remaining workstation gates

Host tests cover pagination/stable IDs, private paths, links, closed schemas,
opt-in/permissions and unchanged projects. The Windows workflow uses an original
synthetic plugin with deliberate Kontakt routing metadata solely for this test.
It contains no NI code, instruments, samples or Lua. It tests actual SDK editor
calls, native Open dialog, generated path decoding, changed standard state,
guarded application, Undo/Redo/save/reopen, failure and cancellation. The fixture
is neither installed nor included in the portable package.

An independent Lua 5.4 gate executes the actual generated script against a
synthetic `Kontakt` table to check Unicode/quote/backslash injection resistance,
selected arguments, optional snapshots and atomic success/failure receipts. It
does not establish vendor API compatibility. The runtime is not bundled.

Commercial acceptance must separately record actual version/class, Developer
setup, licensed instrument/snapshot selection, vendor Lua execution, state size,
sample loading, original MIDI audio/clip/onset/end checks, fresh-worker recall
and human listening. For PLAYBOX, useful controls and generated MIDI output are
additional tests; a loaded sound is not a composition API.

## Primary references

- [Kontakt Lua setup and execution](https://docs.native-instruments.com/online-guides/kontakt-api-reference-manual/en/introduction-and-setup)
- [Official instrument/snapshot loading](https://docs.native-instruments.com/online-guides/kontakt-api-reference-manual/en/instrument)
- [Developer options](https://docs.native-instruments.com/online-guides/kontakt-player-manual/en/options-dialog.html)
- Steinberg SDK `pluginterfaces/gui/iplugview.h`, `base/keycodes.h`, and
  `public.sdk/source/vst/vstpresetfile.h` in the CI-pinned SDK.
- [Windows common-dialog folder query](https://learn.microsoft.com/en-us/windows/win32/dlgbox/cdm-getfolderpath)
