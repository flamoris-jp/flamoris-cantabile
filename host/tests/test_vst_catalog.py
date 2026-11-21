"""Operator-configured fixture worker: catalog isolation and float64 mix math.

Real VST ABI acceptance lives in audio/vst3/tests/band_mcp_smoke.py.
"""
import array
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_host
from test_host import Client, CORE, DOTNET, HOST
from test_vst_safety import environment


@unittest.skipUnless(sys.platform.startswith("linux"), "POSIX executable fixture")
class VstCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HOST.exists() or not CORE.exists() or not Path(DOTNET).exists():
            raise unittest.SkipTest("Build the native core and host first")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plugins = self.root / "plugins"
        self.plugins.mkdir()
        for name in ("a-good.vst3", "b-crash.vst3", "c-timeout.vst3", "unsupported.dll"):
            (self.plugins / name).write_text("fixture")
        self.worker = self.root / "worker"
        self.worker.write_text(f"#!{sys.executable}\n" + '''
import json, pathlib, struct, sys, time
args = sys.argv[1:]
module = pathlib.Path(args[args.index('--plugin') + 1])
if module.name == 'b-crash.vst3': sys.exit(42)
if module.name == 'c-timeout.vst3': time.sleep(60)
if args[0] == 'list':
    classes = [{'id':f'{i:032X}', 'name':f'Fixture {i}', 'vendor':'Test', 'version':'1', 'kind':'instrument'} for i in range(1,5)]
    print(json.dumps({'ok':True,'classes':classes,'instruments':classes}))
elif args[0] == 'inspect':
    print(json.dumps({'ok':True,'render_supported':True,'buses':[
      {'media_type':'event','direction':'input','index':0,'channel_count':16,'bus_type':0},
      {'media_type':'audio','direction':'output','index':0,'channel_count':2,'bus_type':0}]}))
else:
    identity = int(args[args.index('--class-id') + 1],16)
    value = {1:1.5, 2:-1.0, 3:0.125, 4:float('nan')}[identity]
    output = pathlib.Path(args[args.index('--output') + 1])
    data = struct.pack('<200f', *([value]*200))
    output.write_bytes(struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',36+len(data),b'WAVE',b'fmt ',16,3,2,48000,384000,8,32,b'data',len(data))+data)
    print(json.dumps({'ok':True,'backend':'vst3','sample_format':'ieee_float32le'}))
''', encoding="utf-8")
        self.worker.chmod(0o700)
        with environment(CANTABILE_VST3_PATHS=str(self.plugins), CANTABILE_VST3_SCAN_TIMEOUT_MS="500",
                         CANTABILE_VST3_MODULE=None, CANTABILE_VST3_PRESET=None, CANTABILE_VST3_CLASS_ID=None), \
                patch.object(test_host, "RENDERER", self.worker):
            self.client = Client(self.root / "workspace")

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def wait(self, job):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = self.client.tool("vst_job_status", {"job_id": job["job_id"]}, guard=False)
            if status["state"] in {"completed", "failed", "cancelled"}:
                return status
            time.sleep(0.02)
        self.fail("Job deadline")

    def scan(self):
        status = self.wait(self.client.tool("vst_scan", {}))
        self.assertEqual(status["state"], "completed", status)
        return status["result"]

    def test_catalog_retains_worker_crash_timeout_and_host_only_paths(self):
        catalog = self.scan()
        self.assertEqual(len(catalog["modules"]), 3)
        self.assertEqual(len(catalog["instruments"]), 4)
        self.assertEqual(sum(m["loaded"] for m in catalog["modules"]), 1)
        self.assertIn("vst_timeout", [m["error"] for m in catalog["modules"]])
        self.assertNotIn(str(self.root), json.dumps(catalog))
        self.assertIn("vst2", catalog["unsupported_formats"])
        private = json.loads((self.root / "workspace/vst3-catalog.json").read_text())
        self.assertIn(str(self.plugins), json.dumps(private))
        readonly = self.client.tool("instruments_list", {}, guard=False)["catalog"]
        self.assertEqual(readonly, catalog)

    def test_mix_accumulates_unclipped_float_then_measures_pcm_clipping(self):
        catalog = self.scan()
        self.client.tool("edit_batch", {"commands": [{"op": "track.add", "track": {"id": f"p{i}", "events": [
            {"kind": "note", "tick": 0, "duration": 480, "pitch": 60, "velocity": 100}]}} for i in range(3)]})
        for i, instrument in enumerate(catalog["instruments"][:3]):
            self.client.tool("instrument_assign", {"track_id": f"p{i}", "catalog_id": instrument["catalog_id"]})
        def render():
            status = self.wait(self.client.tool("audio_render", {}))
            self.assertEqual(status["state"], "completed", status)
            return status["result"]
        result = render()
        self.assertEqual(result["audio"]["peak"], 0.625)
        self.assertEqual(result["audio"]["clipped_samples"], 0)
        data = self.client.download(result["artifact"]["file_id"], chunk=131072)
        pcm = array.array("h"); pcm.frombytes(data[44:])
        self.assertEqual(set(pcm), {round(0.625 * 32767)})
        self.client.tool("edit_batch", {"commands": [{"op": "track.update", "track_id": "p0", "patch": {"volume": 4}}]})
        clipped = render()
        self.assertEqual(clipped["audio"]["peak"], 5.125)
        self.assertEqual(clipped["audio"]["clipped_samples"], 200)
        data = self.client.download(clipped["stems"][0]["artifact"]["file_id"], chunk=131072)
        floating = array.array("f"); floating.frombytes(data[44:]); self.assertEqual(set(floating), {6.0})
        self.assertTrue(any(i["last_render_audible"] for i in self.client.tool("instruments_list", {}, guard=False)["catalog"]["instruments"]))
        # NaNs are rejected without publishing a partial band.
        before = self.client.tool("file_list", {}, guard=False)["files"]
        self.client.tool("instrument_assign", {"track_id": "p2", "catalog_id": catalog["instruments"][3]["catalog_id"]})
        failed = self.wait(self.client.tool("audio_render", {}))
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["error"]["code"], "invalid_audio")
        self.assertEqual(self.client.tool("file_list", {}, guard=False)["files"], before)
        # Missing installed module is tied to the failing track, never substituted.
        (self.plugins / "a-good.vst3").unlink()
        missing = self.wait(self.client.tool("audio_render", {}))
        self.assertEqual(missing["error"]["code"], "instrument_missing")
        self.assertEqual(missing["error"]["track_id"], "p0")
        self.assertEqual(self.client.tool("file_list", {}, guard=False)["files"], before)


if __name__ == "__main__":
    unittest.main()
