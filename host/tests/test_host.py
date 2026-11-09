"""Real SDK stdio -> shared Core authorization -> C++ session integration tests."""
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import wave

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "host/bin/Release/net10.0/cantabile-host.dll"
DOTNET = os.environ.get("CANTABILE_DOTNET") or shutil.which("dotnet") or "/tmp/cantabile-dotnet/dotnet"
CORE = Path(os.environ.get("CANTABILE_CORE_LIBRARY", str(ROOT / "build/libcantabile_core.so")))
RENDERER = Path(os.environ.get("CANTABILE_RENDERER", str(ROOT / "build/audio/cantabile-render")))
SOUNDFONT = Path(os.environ.get("CANTABILE_SOUNDFONT", "/usr/share/sounds/sf2/TimGM6mb.sf2"))


class Client:
    def __init__(self, workspace, permission="edit"):
        env = dict(os.environ, CANTABILE_CORE_LIBRARY=str(CORE), CANTABILE_RENDERER=str(RENDERER), CANTABILE_SOUNDFONT=str(SOUNDFONT))
        self.errors = tempfile.TemporaryFile()
        self.process = subprocess.Popen([DOTNET, str(HOST), "stdio", "--workspace", str(workspace), "--permission", permission],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors, text=True, env=env)
        self.messages = queue.Queue()
        self.counter = 0
        def read():
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except json.JSONDecodeError:
                    self.messages.put({"unexpected_stdout": line[:500]})
            self.messages.put({"closed": True})
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        response = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "cantabile-integration", "version": "1"}})
        if "error" in response:
            raise AssertionError(response)
        self.notify("notifications/initialized", {})

    def notify(self, method, params):
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
        self.process.stdin.flush()

    def rpc(self, method, params):
        self.counter += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 15
        while True:
            response = self.messages.get(timeout=max(0.1, deadline - time.monotonic()))
            if "unexpected_stdout" in response or "closed" in response:
                self.errors.seek(0)
                raise AssertionError((response, self.errors.read().decode()[:2000]))
            if response.get("id") == self.counter:
                return response

    def context(self):
        return self.tool("mcp.context", {}, guard=False)

    def tool_result(self, name, value, guard=True, stale=None):
        arguments = {} if name == "mcp.context" else {"input": value}
        if guard and name != "mcp.context":
            context = self.context()
            arguments["guard"] = {"runtimeId": context["runtimeId"], "documentToken": context["documentToken"],
                                  "expectedRevision": context["revision"] if stale is None else str(stale)}
        response = self.rpc("tools/call", {"name": name, "arguments": arguments})
        if "error" in response:
            raise AssertionError(response)
        return response["result"]

    def tool(self, name, value, guard=True):
        response = self.tool_result(name, value, guard)
        if response.get("isError"):
            raise AssertionError(response)
        return response.get("structuredContent") or json.loads(response["content"][0]["text"])

    def download(self, file_id, chunk=7):
        offset, output = 0, bytearray()
        while True:
            result = self.tool("file_read", {"file_id": file_id, "offset": offset, "max_bytes": chunk})
            data = base64.b64decode(result["data_base64"])
            assert hashlib.sha256(data).hexdigest() == result["chunk_sha256"]
            output.extend(data)
            offset = result["next_offset"]
            if result["complete"]:
                assert hashlib.sha256(output).hexdigest() == result["sha256"]
                return bytes(output)

    def close(self):
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        if self.process.stdout:
            self.process.stdout.close()
        self.errors.close()
        self.reader.join(timeout=1)


class HostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HOST.exists() or not CORE.exists() or not Path(DOTNET).exists():
            raise unittest.SkipTest("Build the native core and .NET host before running host integration tests.")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = Client(self.temp.name)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def load_note(self):
        return self.client.tool("project_load", {"discard_unsaved": True, "data": {"schema_version": 1, "ppq": 480, "tracks": [
            {"id": "melody", "name": "Melody", "channel": 0, "events": [{"id": "note", "kind": "note", "tick": 0, "pitch": 60,
            "duration": 480, "velocity": 100, "release_velocity": 0}]}]}})

    def test_official_protocol_discovery_and_readonly_enforcement(self):
        listed = self.client.rpc("tools/list", {})["result"]["tools"]
        self.assertIn("project_get", {x["name"] for x in listed})
        self.client.close()
        self.client = Client(self.temp.name, "readOnly")
        listed = self.client.rpc("tools/list", {})["result"]["tools"]
        self.assertNotIn("edit_batch", {x["name"] for x in listed})
        self.assertFalse(self.client.tool("project_get", {})["can_undo"])
        self.assertTrue(self.client.tool_result("project_save", {})["isError"])

    def test_revision_atomicity_and_shared_history(self):
        before = self.load_note()
        failed = self.client.tool_result("edit_batch", {"commands": [
            {"op": "event.update", "track_id": "melody", "event_id": "note", "patch": {"pitch": 64}},
            {"op": "event.remove", "track_id": "melody", "event_id": "missing"}]})
        self.assertTrue(failed["isError"])
        self.assertEqual(self.client.tool("project_get", {})["project"], before["project"])
        changed = self.client.tool("edit_batch", {"commands": [{"op": "event.update", "track_id": "melody", "event_id": "note", "patch": {"pitch": 67}}]})
        self.assertEqual(changed["project"]["tracks"][0]["events"][0]["pitch"], 67)
        self.assertTrue(self.client.tool_result("edit_batch", {"commands": [{"op": "track.remove", "track_id": "melody"}]}, stale=before["revision"])["isError"])
        undone = self.client.tool("history_undo", {})
        self.assertEqual(undone["project"]["tracks"][0]["events"][0]["pitch"], 60)
        redone = self.client.tool("history_redo", {})
        self.assertEqual(redone["project"], changed["project"])

    def test_over_budget_edit_fails_without_partial_commit(self):
        before = self.load_note()
        result = self.client.tool_result("edit_batch", {"commands": [
            {"op": "event.update", "track_id": "melody", "event_id": "note", "patch": {"pitch": 62}},
            {"op": "track.update", "track_id": "melody", "patch": {"instrument": {"backend": "fluidsynth", "opaque": "x" * (2 * 1024 * 1024)}}}]})
        self.assertTrue(result["isError"])
        self.assertEqual(self.client.tool("project_get", {})["project"], before["project"])
        self.client.tool("project_save", {})

    def test_saved_project_confined_to_workspace_and_restart(self):
        self.load_note()
        self.assertTrue(self.client.tool_result("project_save", {"name": "../escaped.cantabile.json"})["isError"])
        self.assertTrue(self.client.tool_result("project_save", {"name": "bad.cantabile.json\n"})["isError"])
        projects = Path(self.temp.name) / "projects"
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("untouched")
        (projects / "link.cantabile.json").symlink_to(outside)
        self.assertTrue(self.client.tool_result("project_save", {"name": "link.cantabile.json"})["isError"])
        self.assertEqual(outside.read_text(), "untouched")
        self.client.tool("project_save", {})
        expected = self.client.tool("project_get", {})["project"]
        self.client.close()
        self.client = Client(self.temp.name)
        self.assertEqual(self.client.tool("project_get", {})["project"], expected)
        saved = projects / "resident.cantabile.json"
        saved.write_text(saved.read_text() + " ")
        self.assertTrue(self.client.tool_result("project_save", {})["isError"])

    def test_midi_export_chunks_survive_restart_and_release(self):
        self.load_note()
        artifact = self.client.tool("midi_export", {})
        midi = self.client.download(artifact["file_id"])
        self.assertTrue(midi.startswith(b"MThd"))
        self.assertEqual(len(midi), artifact["size"])
        self.client.close()
        self.client = Client(self.temp.name)
        files = self.client.tool("file_list", {})["files"]
        self.assertIn(artifact["file_id"], {x["file_id"] for x in files})
        self.assertEqual(self.client.download(artifact["file_id"]), midi)
        self.assertTrue(self.client.tool("file_release", {"file_id": artifact["file_id"]})["released"])
        self.assertTrue(self.client.tool_result("file_read", {"file_id": artifact["file_id"], "offset": 0})["isError"])

    def test_rendered_wav_and_unsupported_instrument(self):
        if not RENDERER.exists() or not SOUNDFONT.exists():
            self.skipTest("Render worker and configured free SoundFont are required.")
        self.load_note()
        job = self.client.tool("audio_render", {"max_seconds": 10})
        deadline = time.monotonic() + 15
        while True:
            status = self.client.tool("audio_status", {"job_id": job["job_id"]})
            if status["state"] not in ("queued", "running"):
                break
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        self.assertEqual(status["state"], "completed", status)
        data = self.client.download(status["artifact"]["file_id"], chunk=128 * 1024)
        import io
        with wave.open(io.BytesIO(data)) as wav:
            self.assertEqual(wav.getnchannels(), 2)
            self.assertGreater(wav.getnframes(), 24000)
        self.assertGreater(status["audio"]["peak"], 0)
        self.client.tool("edit_batch", {"commands": [{"op": "track.update", "track_id": "melody", "patch": {"instrument": {"backend": "vst3"}}}]})
        self.assertTrue(self.client.tool_result("audio_render", {"max_seconds": 10})["isError"])


if __name__ == "__main__":
    unittest.main()
