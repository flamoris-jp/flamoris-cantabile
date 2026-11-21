"""Real public VST3 -> async MCP band jobs. All media is temporary/generated."""
import array
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "host/tests"))
import test_host


def wait(client, job, expected="completed", timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.tool("vst_job_status", {"job_id": job["job_id"]}, guard=False)
        if status["state"] in ("completed", "failed", "cancelled"):
            assert status["state"] == expected, status
            return status
        time.sleep(0.04)
    raise AssertionError("job deadline")


def main():
    worker, plugin, core = map(lambda p: str(Path(p).resolve()), sys.argv[1:4])
    os.environ.update(CANTABILE_RENDERER=worker, CANTABILE_CORE_LIBRARY=core,
                      CANTABILE_VST3_PATHS=plugin, CANTABILE_VST3_SCRIPT_WRITE="true")
    for name in ("CANTABILE_VST3_MODULE", "CANTABILE_VST3_CLASS_ID", "CANTABILE_VST3_PRESET"):
        os.environ.pop(name, None)
    test_host.RENDERER, test_host.CORE = Path(worker), Path(core)
    with tempfile.TemporaryDirectory() as directory:
        client = test_host.Client(directory)
        try:
            scan = client.tool("vst_scan", {})
            catalog = wait(client, scan)["result"]
            assert plugin not in json.dumps(catalog), "catalog disclosed host path"
            instruments = {i["name"]: i for i in catalog["instruments"] if i["usable"]}
            assert {"mda Piano", "mda EPiano", "mda DX10"} <= instruments.keys(), catalog
            assert any(i["kind"] == "effect" and not i["usable"] for i in catalog["instruments"])
            assert all(i["last_render_audible"] is None for i in catalog["instruments"])
            commands = [{"op": "track.add", "track": {"id": "conductor", "end_tick": 2400, "events": [
                {"kind": "meta", "tick": 0, "meta_type": 81, "data": [7, 161, 32]},
                {"kind": "meta", "tick": 720, "meta_type": 81, "data": [4, 147, 224]},
                {"kind": "meta", "tick": 0, "meta_type": 88, "data": [4, 2, 24, 8]}]}}]
            for i in range(4):
                commands.append({"op": "track.add", "track": {"id": f"part{i}", "channel": i, "render_channel": 0, "volume": 0.75,
                    "events": [{"kind": "note", "tick": i * 480, "duration": 480, "pitch": 60 + i * 2, "velocity": 100}]}})
            client.tool("edit_batch", {"commands": commands})
            for i, name in enumerate(("mda Piano", "mda EPiano", "mda DX10", "mda Piano")):
                client.tool("instrument_assign", {"track_id": f"part{i}", "catalog_id": instruments[name]["catalog_id"]})
            # Same plugin, independently saved processor/controller states.
            for track, value in (("part0", 0.1), ("part3", 0.9)):
                script = {"version": 1, "operations": [{"op": "set_parameter", "id": 0, "value": value},
                    {"op": "save_state", "name": "sound.vstpreset"}]}
                job = client.tool("vst_script_begin", {"track_id": track, "script": script})
                result = wait(client, job)["result"]
                state = result["artifacts"][0]
                client.tool("instrument_state_set", {"track_id": track, "file_id": state["file_id"]})
                client.tool("file_release", {"file_id": state["file_id"]})
            project = client.tool("project_get", {}, guard=False)["project"]
            assert project["tracks"][1]["instrument"]["state_base64"] != project["tracks"][4]["instrument"]["state_base64"]
            client.tool("project_save", {})
            client.close(); client = test_host.Client(directory)
            assert client.tool("project_get", {}, guard=False)["project"] == project
            for track, expected in (("part0", 0.1), ("part3", 0.9)):
                result = client.tool("vst_script", {"track_id": track, "script": {"version": 1, "operations": [{"op": "get_parameter", "id": 0}]}}, guard=False)
                assert result["track_id"] == track
                assert abs(result["results"][0]["value"] - expected) < 1e-5, result
            job = client.tool("audio_render", {"max_seconds": 10})
            rendered = wait(client, job)["result"]
            assert len(rendered["stems"]) == 4
            stems = []
            for i, stem in enumerate(rendered["stems"]):
                data = client.download(stem["artifact"]["file_id"], chunk=131072)
                assert struct.unpack_from("<HHI", data, 20) == (3, 2, 48000)
                samples = array.array("f"); samples.frombytes(data[44:]); stems.append(samples)
                assert max(map(abs, samples)) > 0.0001
                onset = next(n // 2 for n, s in enumerate(samples) if abs(s) > 0.0001)
                if i:
                    # i=1 starts at .5s; later tempo changes do not collapse rests.
                    assert onset >= (24000 if i == 1 else 38400), (i, onset)
            assert len({len(s) for s in stems}) == 1
            mixed = client.download(rendered["artifact"]["file_id"], chunk=131072)
            with wave.open(io.BytesIO(mixed)) as wav:
                assert wav.getnframes() * 2 == len(stems[0])
                pcm = array.array("h"); pcm.frombytes(wav.readframes(wav.getnframes()))
            for index in range(0, len(pcm), 97):
                expected = round(max(-1, min(1, sum(s[index] for s in stems))) * 32767)
                assert abs(pcm[index] - expected) <= 1, (pcm[index], expected)
            # Change one sound, retain common timeline and other tracks.
            client.tool("instrument_assign", {"track_id": "part2", "catalog_id": instruments["mda EPiano"]["catalog_id"]})
            rerendered = wait(client, client.tool("audio_render", {"max_seconds": 10}))["result"]
            assert rerendered["audio"]["frames"] == rendered["audio"]["frames"]
            assert rerendered["stems"][0]["artifact"]["sha256"] == rendered["stems"][0]["artifact"]["sha256"]
            # A later part failing must publish no subset of new stems/mix.
            before = client.tool("file_list", {}, guard=False)["files"]
            client.tool("edit_batch", {"commands": [{"op": "event.add", "track_id": "part3", "event": {"kind": "midi", "tick": 0, "status": 195, "data": [1]}}]})
            failed = wait(client, client.tool("audio_render", {"max_seconds": 10}), "failed")
            assert failed["error"]["track_id"] == "part3", failed
            assert client.tool("file_list", {}, guard=False)["files"] == before
            client.tool("history_undo", {})
            # Targeted mute/solo/pan route through native edits, including Undo.
            client.tool("edit_batch", {"commands": [{"op": "track.update", "track_id": "part1", "patch": {"solo": True, "pan": -1, "volume": 0.25}}]})
            solo = wait(client, client.tool("audio_render", {"max_seconds": 10}))["result"]
            assert [s["track_id"] for s in solo["stems"]] == ["part1"]
            data = client.download(solo["stems"][0]["artifact"]["file_id"], chunk=131072)
            samples = array.array("f"); samples.frombytes(data[44:]); assert not any(samples[1::2])
            client.tool("history_undo", {})
            # Cancellation after a completed stem still publishes no band output.
            client.close()
            os.environ["CANTABILE_VST3_REALTIME"] = "true"
            client = test_host.Client(directory)
            before = client.tool("file_list", {}, guard=False)["files"]
            job = client.tool("audio_render", {"max_seconds": 10})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                status = client.tool("vst_job_status", {"job_id": job["job_id"]}, guard=False)
                if status["completed_units"] >= 1: break
                time.sleep(0.1)
            else: raise AssertionError(status)
            client.tool("audio_cancel", {"job_id": job["job_id"]})
            cancelled = wait(client, job, "cancelled")
            assert cancelled["result"] is None
            assert client.tool("file_list", {}, guard=False)["files"] == before
            assert not list((Path(directory) / "exports").glob(".vst-*"))
        finally:
            client.close()
            os.environ.pop("CANTABILE_VST3_REALTIME", None)
    print("Band gate passed: discovery, 3 distinct classes/4 independent tracks, saved state/reopen, aligned float stems/PCM mix, sound replacement, solo/pan, later failure, cancellation")


if __name__ == "__main__":
    main()
