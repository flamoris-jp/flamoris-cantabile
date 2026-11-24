"""Real SDK outputEvents -> bounded jobs/artifacts -> native append/history.

Uses the original capture_fixture.cpp; commercial plugin behavior is a separate gate.
"""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "host/tests"))
sys.path.insert(0, str(ROOT / "tests"))
import test_host
from test_native_interop import parse_smf
from band_mcp_smoke import wait


def main():
    worker, plugin, core, mda = (str(Path(p).resolve()) for p in sys.argv[1:5])
    os.environ.update(CANTABILE_RENDERER=worker, CANTABILE_CORE_LIBRARY=core,
                      CANTABILE_VST3_PATHS=plugin, CANTABILE_VST3_SCRIPT_WRITE="true")
    for key in ("CANTABILE_VST3_MODULE", "CANTABILE_VST3_PRESET", "CANTABILE_VST3_CLASS_ID"):
        os.environ.pop(key, None)
    test_host.RENDERER, test_host.CORE = Path(worker), Path(core)
    with tempfile.TemporaryDirectory() as folder:
        client = test_host.Client(folder)
        def project(): return client.tool("project_get", {}, guard=False)
        def failure(tool, value, code=None, **kwargs):
            result = client.tool_result(tool, value, **kwargs)
            assert result.get("isError"), result
            detail = result.get("structuredContent") or json.loads(result["content"][0]["text"])
            if code: assert detail["error"]["code"] == code, detail
        def load(pitch=60, extra=False):
            notes = [{"id":"n0","kind":"note","tick":10,"duration":950,"pitch":pitch,"velocity":99,"channel":2,"release_velocity":17}]
            if extra: notes += [{"id":"n1","kind":"note","tick":20,"duration":200,"pitch":pitch,"velocity":77,"channel":2}]
            client.tool("project_load", {"discard_unsaved":True,"data":{"schema_version":1,"ppq":480,"tracks":[
                {"id":"time","events":[{"kind":"meta","tick":0,"meta_type":81,"data":[7,161,32]},
                    {"kind":"meta","tick":480,"meta_type":81,"data":[3,208,144]},
                    {"kind":"meta","tick":480,"meta_type":88,"data":[3,2,24,8]}]},
                {"id":"source","channel":2,"end_tick":960,"events":notes}]}})
            client.tool("instrument_assign", {"track_id":"source","catalog_id":identity})
        def capture(**fields):
            return wait(client, client.tool("vst_capture_begin", {"track_id":"source","max_seconds":5,**fields}))["result"]
        def pair(result): return {"midi_file_id":result["midi"]["file_id"],"report_file_id":result["report"]["file_id"]}
        try:
            listed = wait(client, client.tool("vst_scan", {}))["result"]
            identity = next(x["catalog_id"] for x in listed["instruments"] if x["usable"])
            load(extra=True)
            inspector = client.tool("vst_inspect", {"track_id":"source"}, guard=False)["midi_output"]
            assert inspector["available"] and inspector["capture_supported"] and inspector["observed_event_count"] is None, inspector
            before = project()
            first = capture()
            assert first["event_count"] == 4 and first["capture_status"] == "captured", first
            assert project() == before, "capture changed editor/history"
            report = json.loads(client.download(first["report"]["file_id"], chunk=131072))
            assert report["overlapping_note_ons"] == 1 and report["unterminated_notes"] == 0, report
            assert [e["sample_frame"] for e in report["events"]] == [517, 1017, 11017, 36017], report
            assert [e["note_id"] for e in report["events"]] == [100,101,100,101], report
            assert report["events"][-1]["phase"] == "postroll_note_off", report
            assert all(e["channel"] == 2 and e["pitch"] == 72 for e in report["events"])
            data = client.download(first["midi"]["file_id"], chunk=131072)
            parsed = parse_smf(data)
            assert b"MThd" == data[:4] and parsed, parsed
            preview = client.tool("vst_capture_preview", pair(first), guard=False)
            assert preview["event_count"] == 4 and not preview["automatic_reinjection"]
            imported = client.tool("vst_capture_import", {**pair(first),"track_id":"generated","name":"Generated notes"})
            assert imported["project"]["tracks"][:2] == before["project"]["tracks"]
            generated = imported["project"]["tracks"][2]
            assert generated["muted"] and "instrument" not in generated and generated["provenance"]["source_track_id"] == "source"
            assert all(e["kind"] == "midi" for e in generated["events"]), generated
            failure("vst_capture_import", {**pair(first),"track_id":"source","name":"Never overwrite"}, "TRACK_EXISTS")
            assert client.tool("history_undo", {})["project"] == before["project"]
            assert client.tool("history_redo", {})["project"] == imported["project"]
            # Current timing must still agree even if a fresh guard is provided.
            time_id = project()["project"]["tracks"][0]["events"][0]["id"]
            client.tool("edit_batch", {"commands":[{"op":"event.update","track_id":"time","event_id":time_id,"patch":{"data":[7,161,33]}}]})
            failure("vst_capture_import", {**pair(first),"track_id":"badtime","name":"Bad timing"}, "CAPTURE_TIMING_CONFLICT")
            client.tool("history_undo", {})
            client.tool("project_save", {})
            retained = project()["project"]
            client.close(); client = test_host.Client(folder)
            assert project()["project"] == retained
            assert client.download(first["midi"]["file_id"], chunk=131072) == data
            assert client.tool("vst_capture_preview", pair(first), guard=False)["event_count"] == 4
            # Range filtering reports a note-off whose note-on preceded the range.
            load(extra=True)
            ranged = capture(start_tick=200, end_tick=800)
            ranged_report = json.loads(client.download(ranged["report"]["file_id"], chunk=131072))
            assert ranged_report["discarded_before_range"] == 2 and ranged_report["unmatched_note_offs"] == 1, ranged_report
            # Failed/empty jobs cannot publish a partial successful note capture.
            for pitch, code in ((61,"UNSUPPORTED_OUTPUT_EVENT"),(62,"MIDI_OUTPUT_LIMIT"),(63,"INVALID_OUTPUT_EVENT"),(65,"vst_worker_crashed"),(67,"INVALID_OUTPUT_EVENT")):
                load(pitch)
                old = client.tool("file_list", {}, guard=False)
                result = wait(client, client.tool("vst_capture_begin", {"track_id":"source","max_seconds":5}), expected="failed")
                assert result["error"]["code"] == code, result
                assert client.tool("file_list", {}, guard=False) == old
            load(64)
            empty = capture(); assert empty["capture_status"] == "no_events" and empty["event_count"] == 0
            failure("vst_capture_import", {**pair(empty),"track_id":"empty","name":"Empty"}, "capture_empty")
            failure("vst_capture_import", {"midi_file_id":first["midi"]["file_id"],"report_file_id":empty["report"]["file_id"],"track_id":"mixed","name":"Mixed"}, "capture_artifact_mismatch")
            load(extra=True)
            limited = wait(client, client.tool("vst_capture_begin", {"track_id":"source","max_events":2}), expected="failed")
            assert limited["error"]["code"] == "MIDI_OUTPUT_LIMIT", limited
            # Saved state ingress, changed setting restoration and performance metadata.
            script = {"version":1,"operations":[{"op":"set_parameter","id":0,"value":0.5},{"op":"save_state","name":"changed.vstpreset"}]}
            state = wait(client, client.tool("vst_script_begin", {"track_id":"source","script":script}))["result"]["artifacts"][0]
            state_bytes = client.download(state["file_id"], chunk=131072)
            client.tool("instrument_state_import", {"track_id":"source","data_base64":base64.b64encode(state_bytes).decode()})
            assert client.tool("vst_inspect", {"track_id":"source"}, guard=False)["parameters"][0]["value"] == 0.5
            shelf = client.tool("sound_register", {"track_id":"source","display_name":"Fixture changed","category":"test","classification_source":"Original fixture"})
            performance = {"version":1,"note_numbering":"midi_0_127","library":"Original fixture","source":"capture_fixture.cpp",
                "requires_playing":True,"tempo_sync":True,"controls":[{"id":"transpose","name":"Transpose","kind":"parameter","parameter_id":0,
                    "source":"public Inspector","verification":"parameter_documented"}]}
            client.tool("sound_performance_set", {"sound_id":shelf["sound_id"],"expected_sha256":shelf["state"]["sha256"],"performance":performance})
            assert client.tool("sound_search", {"control_kind":"parameter"}, guard=False)["total"] == 1
            assert client.tool("sound_search", {"control_kind":"keyswitch"}, guard=False)["total"] == 0
            invalid = copy.deepcopy(performance); invalid["controls"][0]["parameter_id"] = -1
            failure("sound_performance_set", {"sound_id":shelf["sound_id"],"expected_sha256":shelf["state"]["sha256"],"performance":invalid})
            client.tool("sound_assign", {"track_id":"source","sound_id":shelf["sound_id"]})
            assert project()["project"]["tracks"][1]["instrument"]["performance"] == performance
            changed = capture()
            changed_report = json.loads(client.download(changed["report"]["file_id"], chunk=131072))
            assert all(e["pitch"] == 66 for e in changed_report["events"])
            evidence_input = {"sound_id":shelf["sound_id"],"expected_sha256":shelf["state"]["sha256"],"performance":performance,
                "capture_midi_file_id":changed["midi"]["file_id"],"capture_report_file_id":changed["report"]["file_id"]}
            evidence_result = client.tool("sound_performance_set", evidence_input)
            assert evidence_result["performance"]["capture_evidence"]["event_count"]==4
            assert client.tool("sound_search", {"verification":"capture_observed"},guard=False)["total"]==1
            failure("sound_performance_set", {**evidence_input,"capture_midi_file_id":first["midi"]["file_id"],"capture_report_file_id":first["report"]["file_id"]},"sound_capture_mismatch")
            performance=evidence_result["performance"]
            failure("sound_performance_set", {"sound_id":shelf["sound_id"],"expected_sha256":shelf["state"]["sha256"],"performance":performance})
            client.tool("sound_assign", {"track_id":"source","sound_id":shelf["sound_id"]})
            client.tool("file_release", {"file_id":state["file_id"]})
            client.tool("project_save", {})
            client.close(); client = test_host.Client(folder)
            assert client.tool("sound_search", {"category":"test"}, guard=False)["sounds"][0]["performance"] == performance
            assert client.tool("vst_inspect", {"track_id":"source"}, guard=False)["parameters"][0]["value"] == 0.5
            recalled = capture()
            recalled_report = json.loads(client.download(recalled["report"]["file_id"], chunk=131072))
            assert recalled_report["events"] == changed_report["events"]
            # Job input/state really are immutable while the editor remains usable.
            instrument = project()["project"]["tracks"][1]["instrument"]
            instrument["preroll_ms"] = 500
            client.tool("edit_batch", {"commands":[{"op":"track.update","track_id":"source","patch":{"instrument":instrument}}]})
            snapshot = client.tool("vst_capture_begin", {"track_id":"source","max_seconds":5})
            client.tool("edit_batch", {"commands":[{"op":"event.update","track_id":"source","event_id":"n0","patch":{"pitch":70}}]})
            snap_result = wait(client, snapshot)["result"]
            snap_report = json.loads(client.download(snap_result["report"]["file_id"],chunk=131072))
            assert all(e["pitch"]==66 for e in snap_report["events"]), snap_report
            client.tool("history_undo", {})
            failure("vst_capture_begin", {"track_id":"source","max_events":"many"})
            failure("instrument_state_import", {"track_id":"source","data_base64":base64.b64encode(b'bad').decode()})
            # Poly pressure is supported through the same real callback.
            client.tool("edit_batch", {"commands":[{"op":"event.add","track_id":"source","event":{"kind":"midi","tick":100,"status":0xa2,"data":[60,55]}}]})
            pressure = capture()
            pressure_report = json.loads(client.download(pressure["report"]["file_id"],chunk=131072))
            assert any(e["type"]==3 and e["pitch"]==66 for e in pressure_report["events"]), pressure_report
            client.tool("history_undo", {})
            # Startup processing and cancellation share the existing supervisor.
            instrument = project()["project"]["tracks"][1]["instrument"]
            instrument["preroll_ms"] = 5000
            client.tool("edit_batch", {"commands":[{"op":"track.update","track_id":"source","patch":{"instrument":instrument}}]})
            old = client.tool("file_list", {}, guard=False)
            job = client.tool("vst_capture_begin", {"track_id":"source","max_seconds":5})
            client.tool("vst_job_cancel", {"job_id":job["job_id"]})
            wait(client, job, expected="cancelled")
            assert client.tool("file_list", {}, guard=False) == old
            failure("vst_capture_begin", {"track_id":"source"}, stale=0)
            # CLI captures the same SDK callback, preserving sub-block offsets.
            midi = Path(folder)/"original.mid"; midi.write_bytes(client.download(client.tool("midi_export", {})["file_id"], chunk=131072))
            # Use the isolated exported source plan from before append.
            load(extra=True)
            midi.write_bytes(client.download(client.tool("midi_export", {})["file_id"], chunk=131072))
            output = Path(folder)/"cli.mid"
            result = subprocess.run([worker,"capture","--plugin",plugin,"--midi",str(midi),"--output",str(output),"--max-seconds","5"], capture_output=True, text=True, timeout=15)
            assert result.returncode == 0, result.stdout + result.stderr
            assert json.loads(result.stdout)["event_count"] == 4
            # No-output-bus is distinct from empty supported output.
            mda_list = json.loads(subprocess.check_output([worker,"list","--plugin",mda], text=True))
            piano = next(x for x in mda_list["instruments"] if x["name"]=="mda Piano")
            unavailable = subprocess.run([worker,"capture","--plugin",mda,"--class-id",piano["id"],"--midi",str(midi),"--output",str(Path(folder)/"no.mid")], capture_output=True, text=True, timeout=15)
            assert unavailable.returncode != 0 and json.loads(unavailable.stdout)["error"]["code"] == "MIDI_OUTPUT_UNAVAILABLE", unavailable.stdout
            client.close(); client = test_host.Client(folder, permission="readOnly")
            names = {t["name"] for t in client.rpc("tools/list", {})["result"]["tools"]}
            assert "vst_capture_preview" in names and "vst_capture_begin" not in names and "vst_capture_import" not in names
            print("Real SDK capture, CLI, artifact pairing, native timing/history/restart, state ingress, performance declarations and cancellation passed")
        finally:
            client.close()


if __name__ == "__main__": main()
