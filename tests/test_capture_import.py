"""Native-only capture import and declaration validation; no plugin substitute."""
import base64
import copy
import unittest
import test_native_interop as interop
from test_native_interop import event, meta, smf


class CaptureImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        interop.NativeInteropTests.setUpClass(); cls.lib = interop.NativeInteropTests.lib
    setUp = interop.NativeInteropTests.setUp
    tearDown = interop.NativeInteropTests.tearDown
    raw = interop.NativeInteropTests.raw
    call = interop.NativeInteropTests.call

    def fixture(self):
        p = {"schema_version":1,"ppq":480,"tracks":[{"id":"original","events":[
            {"kind":"meta","tick":0,"meta_type":81,"data":[7,161,32]},
            {"kind":"note","tick":10,"duration":400,"pitch":60,"velocity":100}]}]}
        self.call("project.load", mutate=True, data=p)
        conductor = meta(0,81,[7,161,32])+meta(480,47,[])
        notes = event(0,[0x92,72,100])+event(100,[0x92,72,90])+event(100,[0x82,72,17])+event(200,[0x82,72,0])+meta(80,47,[])
        origin = dict(kind="vst3_capture",version=1,source_track_id="original",catalog_id="fixture",class_id="0"*32,
            plugin_version="1",source_revision=1,midi_sha256="a"*64,input_sha256="b"*64,state_sha256="c"*64,start_tick=0,end_tick=480)
        return dict(op="track.import_midi",track_id="captured",name="Captured",provenance=origin,
            data_base64=base64.b64encode(smf([conductor,notes])).decode())

    def test_append_preserves_overlap_raw_order_origin_and_undo(self):
        command = self.fixture(); before=self.call("project.get")["project"]
        after=self.call("edit.batch",mutate=True,commands=[command])["project"]
        self.assertEqual(after["tracks"][0],before["tracks"][0])
        t=after["tracks"][1]; self.assertTrue(t["muted"])
        self.assertEqual([e["tick"] for e in t["events"]],[0,100,200,400])
        self.assertEqual([e["status"] for e in t["events"]],[0x92,0x92,0x82,0x82])
        self.assertEqual(t["provenance"],command["provenance"])
        self.assertEqual(self.call("history.undo",mutate=True)["project"],before)
        self.assertEqual(self.call("history.redo",mutate=True)["project"],after)
        self.call("project.load",mutate=True,data=after)
        self.assertEqual(self.call("project.get")["project"],after)

    def test_bad_timing_id_provenance_and_budget_are_atomic(self):
        command=self.fixture(); before=self.call("project.get")
        variants=[]
        bad=copy.deepcopy(command); bad["track_id"]="original"; variants.append(bad)
        bad=copy.deepcopy(command); bad["provenance"]["state_sha256"]="not-a-digest"; variants.append(bad)
        bad=copy.deepcopy(command); data=base64.b64decode(bad["data_base64"]); bad["data_base64"]=base64.b64encode(data[:12]+b'\x00\xf0'+data[14:]).decode(); variants.append(bad)
        for bad in variants:
            result=self.call("edit.batch",mutate=True,commands=[bad]); self.assertFalse(result["ok"],result)
            self.assertEqual(self.call("project.get"),before)
        result=self.call("edit.batch",mutate=True,commands=[command],max_project_bytes=100)
        self.assertFalse(result["ok"]); self.assertEqual(self.call("project.get"),before)

    def test_performance_is_closed_bounded_and_independent_of_audibility(self):
        valid={"version":1,"note_numbering":"midi_0_127","source":"Synthetic manual","controls":[
            {"id":"pattern","name":"Pattern","kind":"pattern","notes":[36,38],"channel":2,"source":"Synthetic manual","verification":"midi_documented"},
            {"id":"control","name":"CC","kind":"cc","cc":1,"source":"Synthetic manual","verification":"unverified"}]}
        self.assertTrue(self.call("performance.validate",data=valid)["ok"])
        for change in (lambda p:p.update(version=2),lambda p:p.update(note_numbering="C3"),lambda p:p.update(shell="invalid"),
                       lambda p:p["controls"][0].update(notes=[128]),lambda p:p["controls"][0].update(parameter_id=4),
                       lambda p:p["controls"][1].update(verification="operator_verified")):
            bad=copy.deepcopy(valid); change(bad); self.assertFalse(self.call("performance.validate",data=bad)["ok"],bad)

    def test_equal_tick_timing_compares_export_order_not_array_order(self):
        command=self.fixture(); p=self.call("project.get")["project"]
        p["tracks"][0]["events"][0]["order"]=10
        p["tracks"][0]["events"].append({"kind":"meta","tick":0,"order":0,"meta_type":81,"data":[3,208,144]})
        self.assertTrue(self.call("project.load",mutate=True,data=p)["ok"])
        conductor=meta(0,81,[3,208,144])+meta(0,81,[7,161,32])+meta(480,47,[])
        notes=event(10,[0x90,72,100])+event(400,[0x80,72,0])+meta(70,47,[])
        command["data_base64"]=base64.b64encode(smf([conductor,notes])).decode()
        result=self.call("edit.batch",mutate=True,commands=[command]); self.assertTrue(result["ok"],result)

    def test_tempo_conflict_during_retained_postroll_is_atomic(self):
        command=self.fixture(); p=self.call("project.get")["project"]
        p["tracks"][0]["events"].append({"id":"later","kind":"meta","tick":500,"meta_type":81,"data":[15,66,64]})
        self.assertTrue(self.call("project.load",mutate=True,data=p)["ok"])
        conductor=meta(0,81,[7,161,32])+meta(500,81,[15,66,64])+meta(38,47,[])
        notes=event(10,[0x90,72,100])+event(528,[0x80,72,0])+meta(0,47,[])
        command["data_base64"]=base64.b64encode(smf([conductor,notes])).decode()
        self.assertTrue(self.call("edit.batch",mutate=True,commands=[command])["ok"])
        self.call("history.undo",mutate=True)
        self.call("edit.batch",mutate=True,commands=[{"op":"event.update","track_id":"original","event_id":"later","patch":{"data":[11,113,176]}}])
        before=self.call("project.get")
        result=self.call("edit.batch",mutate=True,commands=[command])
        self.assertFalse(result["ok"],result)
        self.assertEqual(result["error"]["code"],"CAPTURE_TIMING_CONFLICT")
        self.assertEqual(self.call("project.get"),before)


if __name__=="__main__": unittest.main()
