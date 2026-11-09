"""File-CLI round-trip limits, including the native request-envelope reserve."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NATIVE_LIMIT = 32 * 1024 * 1024
FILE_BUDGET = NATIVE_LIMIT - 4096


def executable():
    configured = os.environ.get("CANTABILE_CLI")
    if configured:
        return Path(configured)
    name = "cantabile-cli.exe" if os.name == "nt" else "cantabile-cli"
    for directory in (ROOT / "build", ROOT / "build" / "Release", ROOT / "build" / "Debug"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    raise RuntimeError("Build cantabile-cli before running the file CLI tests")


class NativeCliLimitsTests(unittest.TestCase):
    def test_near_limit_project_edit_save_and_reopen(self):
        # Use real opaque project metadata to exercise parsing, the C ABI request
        # envelope, native byte budget, atomic disk save and the next load.
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            project = directory / "song.json"
            commands = directory / "commands.json"
            payload = {"schema_version": 1, "ppq": 480, "tracks": [],
                       "name": "Boundary", "smf_format": 1,
                       "padding": "x" * (FILE_BUDGET - 512)}
            project.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            commands.write_text('[]', encoding="utf-8")
            result = subprocess.run([str(executable()), "edit", str(project), str(commands)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertLessEqual(project.stat().st_size, FILE_BUDGET)
            self.assertFalse(project.read_bytes().endswith(b"\n"))
            result = subprocess.run([str(executable()), "validate", str(project)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertTrue(json.loads(result.stdout)["ok"])

    def test_request_file_cannot_bypass_project_envelope_reserve(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            request = {"op": "project.load", "expected_revision": 0,
                       "max_project_bytes": NATIVE_LIMIT,
                       "data": {"schema_version": 1, "ppq": 480, "tracks": [],
                                "padding": "x" * (FILE_BUDGET + 512)}}
            path.write_text(json.dumps(request, separators=(",", ":")), encoding="utf-8")
            self.assertLess(path.stat().st_size, NATIVE_LIMIT)
            result = subprocess.run([str(executable()), "request", str(path)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "LIMIT_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
