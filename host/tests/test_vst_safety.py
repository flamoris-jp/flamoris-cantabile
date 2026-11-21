"""MCP supervisor failure boundaries using a deliberately faulty trusted worker.

The temporary executable is configured by the test operator. It is never supplied
through a DSL operation, and no production interpreter capability is introduced.
Real VST processing remains covered by audio/vst3/tests/*smoke.py.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_host
from test_host import Client, CORE, DOTNET, HOST


@contextmanager
def environment(**settings):
    previous = {name: os.environ.get(name) for name in settings}
    try:
        for name, value in settings.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def script(*operations, timeout=1000):
    return {"script": {"version": 1, "operations": list(operations), "limits": {"timeout_ms": timeout}}}


@unittest.skipUnless(sys.platform.startswith("linux"), "The faulty executable fixture uses a POSIX shebang and /proc.")
class VstSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HOST.exists() or not CORE.exists() or not Path(DOTNET).exists():
            raise unittest.SkipTest("Build the native core and .NET host first.")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.module = self.root / "test.vst3"
        self.module.mkdir()
        self.worker = self.root / "trusted-test-worker"
        self.marker = self.root / "worker-started.json"
        self.client = None

    def tearDown(self):
        if self.client:
            self.client.close()
        self.temporary.cleanup()

    def start(self, body, *, write=False, permission="edit"):
        source = (f"#!{sys.executable}\n"
                  "import json, os, pathlib, subprocess, sys, time\n"
                  f"marker = pathlib.Path({str(self.marker)!r})\n"
                  "marker.write_text(json.dumps({'pid':os.getpid()}))\n" + body + "\n")
        self.worker.write_text(source, encoding="utf-8")
        self.worker.chmod(0o700)
        with environment(CANTABILE_RENDERER=str(self.worker), CANTABILE_VST3_MODULE=str(self.module),
                         CANTABILE_VST3_PRESET=None, CANTABILE_VST3_CLASS_ID=None,
                         CANTABILE_VST3_SCRIPT_WRITE="true" if write else None,
                         CANTABILE_VST3_REALTIME=None, CANTABILE_VST3_WARMUP_MS=None), \
                patch.object(test_host, "RENDERER", self.worker):
            self.client = Client(self.root / "workspace", permission=permission)
        return self.client

    def failure(self, response, code):
        self.assertTrue(response.get("isError"), response)
        value = response.get("structuredContent") or json.loads(response["content"][0]["text"])
        self.assertEqual(value["error"]["code"], code, value)

    def healthy_after_failure(self):
        self.assertIn("project", self.client.tool("project_get", {}, guard=False))
        self.assertEqual(self.client.tool("file_list", {}, guard=False)["files"], [])
        exports = self.root / "workspace" / "exports"
        self.assertFalse(list(exports.glob(".vst-*")), list(exports.iterdir()))
        self.assertFalse(list(exports.rglob("*.part")), list(exports.iterdir()))
        self.assertFalse(list(exports.rglob("*.wav")), list(exports.iterdir()))

    def test_write_optin_denies_before_worker_start(self):
        self.start("raise AssertionError('must never be invoked')")
        result = self.client.tool_result("vst_script_write", script({"op": "set_parameter", "id": 0, "value": 0.5}))
        self.failure(result, "vst_write_disabled")
        self.assertFalse(self.marker.exists())
        self.healthy_after_failure()

    def test_readonly_grant_does_not_advertise_write_tool(self):
        self.start("raise AssertionError('must never be invoked')", write=True, permission="readOnly")
        listed = self.client.rpc("tools/list", {})["result"]["tools"]
        names = {tool["name"] for tool in listed}
        self.assertIn("vst_inspect", names)
        self.assertIn("vst_script", names)
        self.assertNotIn("vst_script_write", names)
        for name in ("vst_scan", "vst_script_begin", "vst_job_cancel", "instrument_assign", "instrument_clear", "instrument_state_set"):
            self.assertNotIn(name, names)
        self.assertTrue(self.client.tool_result("vst_script_write", script({"op": "set_parameter", "id": 0, "value": 0.5}))["isError"])
        self.assertFalse(self.marker.exists())
        self.healthy_after_failure()

    def test_malformed_typed_requests_fail_before_spawn(self):
        self.start("raise AssertionError('must never be invoked')", write=True)
        invalid = [
            {"script": None},
            {"script": {"version": True, "operations": [{"op": "inspect"}]}},
            {"script": {"version": 1.5, "operations": [{"op": "inspect"}]}},
            {"script": {"version": 1, "operations": "inspect"}},
            {"script": {"version": 1, "operations": [{"op": "exec", "command": "forbidden"}]}},
            script({"op": "inspect"}, timeout=True),
            script({"op": "inspect"}, timeout=0.5),
            script({"op": "inspect"}, timeout=120001),
            {**script({"op": "inspect"}), "module": "untrusted.vst3"},
        ]
        for value in invalid:
            with self.subTest(value=value):
                self.failure(self.client.tool_result("vst_script", value, guard=False), "invalid_request")
        self.assertFalse(self.marker.exists())
        self.healthy_after_failure()

    def test_readonly_script_rejects_mutation_before_spawn(self):
        self.start("raise AssertionError('must never be invoked')", write=True)
        for operation in ({"op": "set_parameter", "id": 0, "value": 0.5},
                          {"op": "save_state", "name": "tone.vstpreset"},
                          {"op": "render", "output": "test.wav"},
                          {"op": "midi", "path": "input.mid"}):
            with self.subTest(operation=operation):
                self.failure(self.client.tool_result("vst_script", script(operation), guard=False), "vst_write_required")
        self.assertFalse(self.marker.exists())
        self.healthy_after_failure()

    def test_hard_timeout_kills_worker_and_descendant(self):
        self.start(
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "marker.write_text(json.dumps({'pid':os.getpid(), 'child':child.pid}))\n"
            "time.sleep(120)"
        )
        started = time.monotonic()
        result = self.client.tool_result("vst_script", script({"op": "inspect"}, timeout=800), guard=False)
        self.failure(result, "vst_timeout")
        self.assertLess(time.monotonic() - started, 5)
        processes = json.loads(self.marker.read_text())
        self.assertIn("child", processes)
        for pid in processes.values():
            state = Path(f"/proc/{pid}/stat")
            if state.exists():
                self.assertIn(state.read_text().split()[2], {"Z", "X"}, f"Worker descendant {pid} is still running.")
        self.healthy_after_failure()

    def test_worker_crash_isolated_and_host_remains_usable(self):
        self.start("os._exit(17)")
        self.failure(self.client.tool_result("vst_script", script({"op": "inspect"}), guard=False), "vst_worker_crashed")
        self.assertTrue(self.marker.exists())
        self.healthy_after_failure()

    def test_worker_output_caps_abort_promptly(self):
        # Test stdout and stderr separately: both streams must be bounded/drained.
        for stream, size in (("stdout", 9 * 1024 * 1024), ("stderr", 20 * 1024)):
            with self.subTest(stream=stream):
                if self.client:
                    self.client.close()
                    self.client = None
                self.start(f"sys.{stream}.write('x' * {size})\nsys.{stream}.flush()\ntime.sleep(120)")
                started = time.monotonic()
                self.failure(self.client.tool_result("vst_script", script({"op": "inspect"}, timeout=5000), guard=False), "vst_output_limit")
                self.assertLess(time.monotonic() - started, 5)
                self.healthy_after_failure()

    def test_script_requires_phase1_success(self):
        self.start("print(json.dumps({'ok':True, 'phase1_passed':False, 'results':[], 'files':[]}))")
        self.failure(self.client.tool_result("vst_script", script({"op": "inspect"}), guard=False), "vst_phase1_required")
        self.healthy_after_failure()

    def test_readonly_worker_cannot_publish_a_wav(self):
        self.start("pathlib.Path('unauthorized.wav').write_bytes(b'not audio')\n"
                   "print(json.dumps({'ok':True, 'phase1_passed':True, 'results':[], 'files':[{'name':'unauthorized.wav','kind':'wav'}]}))")
        self.failure(self.client.tool_result("vst_script", script({"op": "inspect"}), guard=False), "vst_write_required")
        self.healthy_after_failure()

    def wait_job(self, job, timeout=18):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.client.tool("vst_job_status", {"job_id": job["job_id"]}, guard=False)
            if status["state"] in {"completed", "cancelled", "failed"}:
                return status
            time.sleep(0.03)
        self.fail("Job did not finish")

    def test_async_script_outlives_request_and_keeps_editor_responsive(self):
        self.start("time.sleep(11)\nprint(json.dumps({'ok':True,'phase1_passed':True,'results':[],'files':[]}))", write=True)
        started = time.monotonic()
        job = self.client.tool("vst_script_begin", script({"op": "inspect"}, timeout=14000))
        self.assertLess(time.monotonic() - started, 2)
        self.assertIn("project", self.client.tool("project_get", {}, guard=False))
        self.assertEqual(self.wait_job(job)["state"], "completed")
        self.assertGreater(time.monotonic() - started, 10)
        self.healthy_after_failure()

    def test_async_failure_preserves_typed_rollback_and_scrubs_plugin_text(self):
        self.start("print(json.dumps({'ok':False,'error':{'code':'VST_PROCESS_FAILED',"
                   "'message':'private plugin text /secret/location','rollback':{'attempted':True,'restored':False,'raw':'secret'},"
                   "'audit':[{'index':0,'op':'set_parameter','status':'failed','raw':'secret'}]}}))\nsys.exit(1)", write=True)
        job = self.client.tool("vst_script_begin", script({"op": "set_parameter", "id": 0, "value": 0.1}))
        status = self.wait_job(job)
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["error"]["rollback"], {"attempted": True, "restored": False, "status": "failed"})
        self.assertEqual(status["error"]["audit"], [{"index": 0, "op": "set_parameter", "status": "failed"}])
        self.assertNotIn("secret", json.dumps(status))
        self.healthy_after_failure()

    def test_async_cancel_kills_worker_and_reports_unknown_rollback(self):
        self.start("time.sleep(120)", write=True)
        job = self.client.tool("vst_script_begin", script({"op": "inspect"}, timeout=120000))
        deadline = time.monotonic() + 3
        while not self.marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.client.tool("vst_job_cancel", {"job_id": job["job_id"]})
        status = self.wait_job(job)
        self.assertEqual(status["state"], "cancelled")
        self.assertEqual(status["error"]["rollback"]["status"], "unknown")
        self.healthy_after_failure()


if __name__ == "__main__":
    unittest.main()
