# Offline audio worker

`cantabile-render` renders an SMF Type 0/1 MIDI file through an explicitly selected
SoundFont into a stereo PCM16 WAV. It uses the FluidSynth 2.x runtime API and the
sample clock. No audio device, MIDI device, display server, GPU, or GUI is opened.

The editor does not link against FluidSynth. The separate worker loads the runtime
library for one render, cleans up its player/synth/settings, and exits. The runtime
module is deliberately retained until process exit: unloading its process-wide
SoundFont dependency state before exit caused native cleanup races in testing.

## Build and runtime

The parent CMake build includes this directory and installs `cantabile-render`.
Only a C++20 compiler and the platform dynamic-loader library are needed to build
this worker; FluidSynth headers are not copied or vendored.

On Debian/Ubuntu, the runtime package is `libfluidsynth3`. Supply a separately
installed SoundFont whose terms suit the intended use. No SoundFont is stored in
this repository. FluidSynth itself is a separately licensed third-party runtime.

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
build/audio/cantabile-render \
  --midi /work/song.mid \
  --soundfont /soundfonts/selected.sf2 \
  --output /work/song.wav \
  --sample-rate 48000 \
  --tail-seconds 2 \
  --max-seconds 600
```

The parent directory must already exist, and the output must not exist. The worker
stages a private `OUTPUT.wav.part` in the same directory and publishes using an atomic,
no-overwrite hard link. Filesystems without that capability return an explicit
`OUTPUT_FAILED` instead of weakening the no-overwrite guarantee. Failed or
cooperatively cancelled renders remove the private temporary output. A forced
process kill cannot run cleanup; the job-owning host must remove its exact
`OUTPUT.wav.part` after the worker has exited. An already-existing staging path
is rejected without deleting or adopting it.

## Result contract

Success is exit status 0 and one JSON stdout line, for example:

```json
{"ok":true,"backend":"fluidsynth","output":"/work/song.wav","sample_rate":48000,"channels":2,"sample_format":"pcm_s16le","frames":168000,"duration_seconds":3.5,"music_seconds":1.5,"peak":0.1,"clipped_samples":0}
```

Errors are nonzero exit status and one JSON stdout line:

```json
{"ok":false,"error":{"code":"INVALID_MIDI","message":"Truncated MTrk chunk"}}
```

FluidSynth diagnostics may also appear on stderr. Host adapters must not treat
stderr text alone as a successful render or expose deployment paths to clients.
The host owns artifact IDs, digest calculation, authorizations, bounded retrieval,
revision snapshots, and publication to the operator's persistent workspace.

On POSIX systems, SIGINT/SIGTERM requests cooperative cancellation. Exit status
130 and `CANCELLED` identify that outcome. Cancellation received during SoundFont
loading is observed after native initialization/one processing block, so cleanup
does not tear down a half-initialized sample-clock player. The host should also
enforce a wall-clock timeout around every worker process.

## Bounds and supported behavior

- Input: at most 16 MiB, 256 tracks and 1,000,000 events, SMF Type 0/1 with PPQ
  timing. The track/header/event structure, running status, tempo lengths and
  positive tempo values, and terminal end-of-track events are checked before
  native playback. Unsupported formats/timing return explicit errors.
- SoundFont: explicitly configured regular file up to 1 GiB. Its program/bank
  interpretation belongs to that bank and FluidSynth.
- Sample rate: integer 8,000–192,000 Hz, default 48,000. Output is stereo PCM16;
  non-finite samples fail; clipping is counted and clamped, not normalized away.
- Duration: computed from all track endpoints and the tempo map, plus an explicit
  0–30 second tail. Default maximum is 600 seconds, configurable up to 3,600.
  The duration cap is checked before creating an output. RIFF size limits apply.
- Reverb and chorus are disabled for the initial backend. MIDI program changes,
  channel controls, pedals, pressure, pitch bend and SoundFont synthesis are
  performed by FluidSynth's MIDI player. The editing core still owns the MIDI.
- This worker renders the supplied MIDI in full. Selected-track/range snapshots
  must be constructed by the host/editor before invoking it; audio trimming and
  per-track preset configuration are not silently inferred.

**VST 3 hosting is not implemented in this slice.** `--backend vst3` returns
`UNSUPPORTED_BACKEND`. FluidSynth is a SoundFont backend, not a VST plugin.
Linux actual rendering is tested. Windows/macOS runtime loading paths exist in
the source but are not accepted as verified renderer platforms yet.

## Verification

```bash
CANTABILE_RENDER="$PWD/build/audio/cantabile-render" \
SOUNDFONT=/path/to/selected.sf2 \
python3 audio/tests/test_renderer.py -v
```

The independent stdlib-only suite creates real SMF0/SMF1 files, renders nonzero
PCM audio, checks tempo changes and 48/44.1 kHz output, and exercises malformed
input, resource bounds, no-overwrite, missing files, explicit unsupported backend,
and POSIX cancellation cleanup. Audio-specific tests report a skip if the
separately installed FluidSynth runtime or SoundFont is unavailable.

On the development Linux environment, real synthesis was verified using the
separately installed `TimGM6mb.sf2`. Its Debian package records GPL-2 terms for
the SoundFont; that bank is not bundled or relicensed as Cantabile code. A release
must select and document its own supplied or user-provided sound assets.

References: [FluidSynth offline rendering](https://www.fluidsynth.org/api/FileRenderer.html),
[MIDI player lifecycle](https://www.fluidsynth.org/api/group__midi__player.html).
