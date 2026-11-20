"""Render SDK MDA Piano with the Cantabile native MIDI path; fixtures stay temporary."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import wave


def invoke(*args, expected_error=None):
    result = subprocess.run(args, text=True, capture_output=True, encoding="utf-8", timeout=45)
    if expected_error:
        assert result.returncode, result.stdout
        assert json.loads(result.stdout)["error"]["code"] == expected_error, result.stdout
        return None
    assert not result.returncode, (result.stdout, result.stderr)
    return json.loads(result.stdout) if result.stdout.strip() else None


def midi_fixture(cli, folder):
    notes = [
        {"kind": "note", "tick": beat * 480, "duration": 360, "pitch": pitch, "velocity": 83}
        for beat, pitch in enumerate([60, 64, 67, 72, 62, 65, 69, 74])
    ]
    project = {
        "schema_version": 1, "name": "VST bridge smoke", "ppq": 480, "smf_format": 1,
        "tracks": [
            {"id": "tempo", "name": "Tempo", "end_tick": 3840, "events": [
                {"kind": "meta", "tick": 1920, "meta_type": 81, "data": [9, 39, 192]},
                {"kind": "meta", "tick": 1920, "meta_type": 88, "data": [3, 2, 24, 8]},
            ]},
            {"id": "piano", "name": "Piano", "end_tick": 3840, "events": notes},
        ],
    }
    source, midi = folder / "score.json", folder / "piano.mid"
    source.write_text(json.dumps(project), encoding="utf-8")
    export = subprocess.run([cli, "export-midi", source, midi], capture_output=True, timeout=15)
    assert export.returncode == 0, export.stderr
    return midi


def main(worker, cli, plugin):
    classes = invoke(worker, "list", "--plugin", plugin)["instruments"]
    piano = next(c for c in classes if "piano" in c["name"].lower() and "epiano" not in c["name"].lower())
    with TemporaryDirectory() as directory:
        folder = Path(directory) / "試聴"
        folder.mkdir()
        midi = midi_fixture(cli, folder.parent)
        target_midi = folder / "ピアノ.mid"
        target_midi.write_bytes(midi.read_bytes())
        midi_hash = hashlib.sha256(target_midi.read_bytes()).digest()
        output = folder / "ピアノ.wav"
        args = worker, "--plugin", plugin, "--class-id", piano["id"], "--midi", target_midi
        report = invoke(*args, "--output", output)
        assert report["backend"] == "vst3" and report["peak"] > 0.01, report
        with wave.open(str(output), "rb") as audio:
            assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (2, 2, 48000)
            # 4 beats at 0.5s + 4 beats at 0.6s + the two-second tail.
            assert audio.getnframes() == 307200
            assert any(audio.readframes(48000))
        assert hashlib.sha256(target_midi.read_bytes()).digest() == midi_hash
        original_wav = hashlib.sha256(output.read_bytes()).digest()
        invoke(*args, "--output", output, expected_error="OUTPUT_EXISTS")
        assert hashlib.sha256(output.read_bytes()).digest() == original_wav
        invoke(*args, "--output", folder / "limit.wav", "--max-seconds", "2", expected_error="DURATION_LIMIT")
        bad_preset = folder / "bad.vstpreset"
        bad_preset.write_bytes(b"bad preset")
        invoke(*args, "--output", folder / "bad.wav", "--preset", bad_preset, expected_error="INVALID_PRESET")
        assert {p.name for p in folder.iterdir()} == {output.name, target_midi.name, bad_preset.name}
    print(f'{piano["name"]}: real VST audio, 307200 frames, peak={report["peak"]:.4f}; MIDI unchanged')


if __name__ == "__main__":
    main(*sys.argv[1:])
