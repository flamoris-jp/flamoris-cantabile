"""Black-box acceptance tests for the offline FluidSynth worker.

Run after building, with CANTABILE_RENDER pointing at cantabile-render.
No MIDI or audio Python packages are required. A system FluidSynth library and
a separately installed SoundFont are needed only by the actual audio tests.
"""

from __future__ import annotations

import array
import ctypes.util
import json
import math
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave


ROOT = Path(__file__).resolve().parents[2]


def vlq(value: int) -> bytes:
    """Encode a fixture delta; fixtures remain independent of the renderer."""
    result = [value & 0x7F]
    value >>= 7
    while value:
        result.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(result)


def event(delta: int, data: bytes) -> bytes:
    return vlq(delta) + data


def smf(*tracks: bytes, midi_format: int = 0, division: int = 480) -> bytes:
    header = b"MThd" + struct.pack(">IHHH", 6, midi_format, len(tracks), division)
    return header + b"".join(
        b"MTrk" + struct.pack(">I", len(track)) + track for track in tracks
    )


EOT = event(0, b"\xff\x2f\x00")
TEMPO_120 = event(0, b"\xff\x51\x03\x07\xa1\x20")
TEMPO_60_AFTER_BEAT = event(480, b"\xff\x51\x03\x0f\x42\x40")


def tempo_change_song() -> bytes:
    # One quarter at 120 BPM, then one at 60 BPM: exactly 1.5 seconds.
    return smf(
        TEMPO_120
        + event(0, b"\xc0\x00")
        + event(0, b"\x90\x3c\x64")
        + TEMPO_60_AFTER_BEAT
        + event(480, b"\x80\x3c\x40")
        + EOT
    )


class RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        configured = os.environ.get("CANTABILE_RENDER")
        candidates = [Path(configured)] if configured else [
            ROOT / "build/audio/cantabile-render",
            ROOT / "build/cantabile-render",
            ROOT / "build/audio/Release/cantabile-render.exe",
            ROOT / "build/Release/cantabile-render.exe",
        ]
        cls.binary = next((p.resolve() for p in candidates if p.is_file()), None)
        if cls.binary is None:
            raise RuntimeError("Build cantabile-render and set CANTABILE_RENDER to its path")
        cls.soundfont = Path(os.environ.get(
            "SOUNDFONT", "/usr/share/sounds/sf2/TimGM6mb.sf2"
        )).resolve()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="cantabile-audio-test-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.midi = self.work / "input.mid"
        self.output = self.work / "output.wav"
        self.midi.write_bytes(tempo_change_song())

    def need_audio_backend(self) -> None:
        if not self.soundfont.is_file():
            self.skipTest("Install a separately licensed SoundFont or set SOUNDFONT")
        if not ctypes.util.find_library("fluidsynth"):
            self.skipTest("Install the FluidSynth runtime for actual audio integration tests")

    def invoke(self, *extra: str, midi: Path | None = None, font: Path | None = None):
        command = [
            str(self.binary), "--midi", str(midi or self.midi),
            "--soundfont", str(font or self.soundfont), "--output", str(self.output),
            *extra,
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"Worker must emit one JSON report: {result.stdout!r}; stderr={result.stderr!r}; {exc}")
        return result, report

    def assert_rejected(self, *extra: str, **kwargs) -> dict:
        result, report = self.invoke(*extra, **kwargs)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIs(report.get("ok"), False, report)
        self.assertIsInstance(report.get("error"), dict, report)
        self.assertTrue(report["error"].get("code"), report)
        self.assertTrue(report["error"].get("message"), report)
        self.assertFalse(self.output.exists(), "Rejected render must not publish a WAV")
        return report

    def assert_audio(self, *, seconds: float, rate: int = 48000) -> dict:
        result, report = self.invoke(
            "--sample-rate", str(rate), "--tail-seconds", "0.25"
        )
        self.assertEqual(result.returncode, 0, f"{report}; stderr={result.stderr}")
        self.assertIs(report.get("ok"), True, report)
        self.assertEqual(report["sample_rate"], rate)
        self.assertEqual(report["channels"], 2)
        self.assertGreater(report["peak"], 0.001)
        self.assertTrue(math.isfinite(report["peak"]))
        self.assertGreaterEqual(report["clipped_samples"], 0)
        with wave.open(str(self.output), "rb") as audio:
            self.assertEqual(audio.getnchannels(), 2)
            self.assertEqual(audio.getsampwidth(), 2)
            self.assertEqual(audio.getframerate(), rate)
            self.assertEqual(audio.getcomptype(), "NONE")
            frame_count = audio.getnframes()
            pcm = audio.readframes(frame_count)
        self.assertLessEqual(abs(frame_count - round(seconds * rate)), 1)
        self.assertEqual(report["frames"], frame_count)
        self.assertAlmostEqual(report["duration_seconds"], frame_count / rate, places=5)
        self.assertEqual(len(pcm), frame_count * 2 * 2)
        samples = array.array("h", pcm)
        if sys.byteorder == "big":
            samples.byteswap()
        self.assertGreater(max(abs(x) for x in samples), 32, "WAV must contain actual synthesized sound")
        # Sound should occur while the note is held, rather than metadata-only
        # output or a burst confined to a release tail.
        middle = samples[rate // 2 : rate]
        self.assertGreater(sum(x * x for x in middle) / len(middle), 1.0)
        return report

    def test_real_audio_respects_tempo_changes_and_tail(self) -> None:
        self.need_audio_backend()
        self.assert_audio(seconds=1.75)

    def test_format_one_uses_conductor_tempo_map(self) -> None:
        self.need_audio_backend()
        conductor = TEMPO_120 + TEMPO_60_AFTER_BEAT + event(480, b"\xff\x2f\x00")
        notes = event(0, b"\x90\x40\x64") + event(960, b"\x80\x40\x40") + EOT
        self.midi.write_bytes(smf(conductor, notes, midi_format=1))
        self.assert_audio(seconds=1.75)

    def test_real_audio_supports_another_sample_rate(self) -> None:
        self.need_audio_backend()
        self.assert_audio(seconds=1.75, rate=44100)

    def test_truncated_track_is_rejected(self) -> None:
        self.midi.write_bytes(tempo_change_song()[:-2])
        self.assert_rejected()

    def test_overlong_delta_vlq_is_rejected(self) -> None:
        self.midi.write_bytes(smf(b"\x81\x80\x80\x80\x00\x90\x3c\x64" + EOT))
        self.assert_rejected()

    def test_running_status_without_channel_status_is_rejected(self) -> None:
        self.midi.write_bytes(smf(event(0, b"\x3c\x64") + EOT))
        self.assert_rejected()

    def test_tempo_with_wrong_payload_length_is_rejected(self) -> None:
        self.midi.write_bytes(smf(event(0, b"\xff\x51\x02\x07\xa1") + EOT))
        self.assert_rejected()

    def test_zero_tempo_is_rejected(self) -> None:
        self.midi.write_bytes(smf(event(0, b"\xff\x51\x03\x00\x00\x00") + EOT))
        self.assert_rejected()

    def test_events_after_end_of_track_are_rejected(self) -> None:
        self.midi.write_bytes(smf(EOT + event(0, b"\x90\x3c\x64")))
        self.assert_rejected()

    def test_missing_end_of_track_is_rejected(self) -> None:
        self.midi.write_bytes(smf(event(0, b"\x90\x3c\x64")))
        self.assert_rejected()

    def test_duration_limit_prevents_publication(self) -> None:
        self.midi.write_bytes(smf(TEMPO_120 + event(480 * 100, b"\xff\x2f\x00")))
        self.assert_rejected("--max-seconds", "1")

    def test_existing_output_is_never_overwritten(self) -> None:
        original = b"existing user data must survive"
        self.output.write_bytes(original)
        result, report = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIs(report.get("ok"), False)
        self.assertEqual(self.output.read_bytes(), original)

    def test_existing_staging_file_is_not_adopted_or_removed(self) -> None:
        self.need_audio_backend()
        part = Path(str(self.output) + ".part")
        part.write_bytes(b"another process owns this staging file")
        self.assert_rejected()
        self.assertEqual(part.read_bytes(), b"another process owns this staging file")

    @unittest.skipUnless(os.name == "posix", "POSIX signal cancellation contract")
    def test_cancel_during_startup_cleans_temporary_output(self) -> None:
        self.need_audio_backend()
        self.midi.write_bytes(smf(
            TEMPO_120 + event(0, b"\x90\x3c\x64")
            + event(480 * 1000, b"\x80\x3c\x00") + EOT
        ))
        process = subprocess.Popen([
            str(self.binary), "--midi", str(self.midi),
            "--soundfont", str(self.soundfont), "--output", str(self.output),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            # The private output is created before native SoundFont loading.
            # Synchronize to a real worker checkpoint rather than killing an
            # uninitialized process before it installs its signal handlers.
            while not Path(str(self.output) + ".part").exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    self.fail("Worker did not reach the rendering checkpoint")
                time.sleep(0.001)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 130, stderr)
            report = json.loads(stdout)
            self.assertFalse(report["ok"])
            self.assertEqual(report["error"]["code"], "CANCELLED")
            self.assertFalse(self.output.exists())
            self.assertFalse(Path(str(self.output) + ".part").exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_unsupported_backend_is_explicit(self) -> None:
        report = self.assert_rejected("--backend", "vst3")
        self.assertEqual(report["error"]["code"], "UNSUPPORTED_BACKEND")

    def test_missing_midi_is_rejected(self) -> None:
        self.assert_rejected(midi=self.work / "absent.mid")

    def test_missing_soundfont_is_rejected(self) -> None:
        self.assert_rejected(font=self.work / "absent.sf2")


if __name__ == "__main__":
    unittest.main()
