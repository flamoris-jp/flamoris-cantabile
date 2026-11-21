"""Real stdio MCP -> isolated VST worker, permissions and immutable state/WAV.

Uses the redistributable SDK test plugin; no commercial presets/media enter Git.
Run after inspector_smoke.py and script_smoke.py have passed.
"""
import base64
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import wave

from smoke import invoke, midi_fixture


@contextmanager
def settings(**values):
    old = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def request(*operations, **limits):
    value = {"version": 1, "operations": list(operations)}
    if limits:
        value["limits"] = limits
    return {"script": value}


def denied(client, name, arguments, guard=True):
    response = client.tool_result(name, arguments, guard=guard)
    assert response.get("isError"), response
    return response


def main(worker, cli, plugin, core):
    piano = next(c for c in invoke(worker, "list", "--plugin", plugin)["instruments"]
                 if c["name"].lower() == "mda piano")
    os.environ.update(CANTABILE_RENDERER=worker, CANTABILE_CORE_LIBRARY=core,
                      CANTABILE_VST3_MODULE=plugin, CANTABILE_VST3_CLASS_ID=piano["id"])
    tests = Path(__file__).resolve().parents[3] / "host" / "tests"
    sys.path.insert(0, str(tests))
    from test_host import Client

    with TemporaryDirectory() as directory, settings(
            CANTABILE_VST3_PRESET=None, CANTABILE_VST3_REALTIME=None,
            CANTABILE_VST3_WARMUP_MS=None, CANTABILE_VST3_SCRIPT_WRITE="true"):
        root = Path(directory)
        midi = midi_fixture(cli, root).read_bytes()
        client = Client(root / "edit")
        try:
            report = client.tool("vst_inspect", {}, guard=False)
            assert report["ok"] and report["parameters"], report
            parameter = next(p for p in report["parameters"] if not p["flags"] & 2 and p["step_count"] == 0)
            pid = parameter["id"]
            # Every script starts from the configured preset. Explicit state artifacts
            # carry settings across calls; a transient change is never global state.
            value = 0.25
            read = client.tool("vst_script", request({"op": "get_parameter", "id": pid}), guard=False)
            assert read["ok"] and read["phase1_passed"], read
            denied(client, "vst_script", request({"op": "set_parameter", "id": pid, "value": value}), guard=False)
            denied(client, "vst_script_write", request({"op": "set_parameter", "id": pid, "value": value}), guard=False)
            denied(client, "vst_inspect", {"plugin": "arbitrary.vst3"}, guard=False)
            denied(client, "vst_script_write", request({"op": "exec", "command": "echo unsafe"}))
            denied(client, "vst_script_write", request({"op": "save_state", "name": "../escape.vstpreset"}))
            denied(client, "vst_script_write", request({"op": "set_parameter", "id": pid, "value": 2}))
            client.tool("midi_import", {"data_base64": base64.b64encode(midi).decode(), "discard_unsaved": False})
            revision = client.context()["revision"]
            result = client.tool("vst_script_write", request(
                {"op": "set_parameter", "id": pid, "value": value},
                {"op": "get_parameter", "id": pid},
                {"op": "save_state", "name": "piano.vstpreset"},
                {"op": "midi", "path": "input.mid"},
                {"op": "render", "output": "piano.wav"}))
            assert result["ok"] and result["phase1_passed"], result
            assert abs(result["results"][1]["value"] - value) < 1e-5, result
            artifacts = {a["kind"]: a for a in result["artifacts"]}
            assert set(artifacts) == {"state", "wav"}, result
            assert client.context()["revision"] == revision, "VST control mutated the MIDI project"
            payload = client.download(artifacts["wav"]["file_id"], chunk=128 * 1024)
            with wave.open(io.BytesIO(payload)) as audio:
                assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()) == (2, 2, 48000, 307200)
                assert any(audio.readframes(48000)), "The initial note was lost"
            preset = client.download(artifacts["state"]["file_id"], chunk=128 * 1024)
            assert preset[:4] == b"VST3", "Not a standard VST3 preset"
            restored = client.tool("vst_script_write", {
                **request({"op": "load_state", "name": "previous.vstpreset"},
                          {"op": "get_parameter", "id": pid}),
                "files": {"previous.vstpreset": artifacts["state"]["file_id"]}})
            assert abs(restored["results"][1]["value"] - value) < 1e-5, restored
            before = client.tool("file_list", {}, guard=False)
            denied(client, "vst_script_write", request(
                {"op": "set_parameter", "id": pid, "value": 0.75},
                {"op": "save_state", "name": "discard.vstpreset"},
                {"op": "render", "midi": "missing.mid", "output": "discard.wav"}))
            assert client.tool("file_list", {}, guard=False) == before, "A failed script published files"
            denied(client, "vst_script", request({"op": "inspect"}, {"op": "inspect"}, max_operations=1), guard=False)
            assert client.tool("vst_inspect", {}, guard=False)["ok"], "Worker failure damaged host"
        finally:
            client.close()
        # Immutable state and WAV survive a compatible host restart.
        client = Client(root / "edit")
        try:
            files = client.tool("file_list", {}, guard=False)["files"]
            assert artifacts["state"]["file_id"] in {f["file_id"] for f in files}, files
            assert client.download(artifacts["state"]["file_id"], chunk=128 * 1024) == preset
        finally:
            client.close()
        client = Client(root / "readonly", permission="readOnly")
        try:
            assert client.tool("vst_inspect", {}, guard=False)["ok"]
            assert client.tool("vst_script", request({"op": "get_parameter", "id": pid}), guard=False)["ok"]
            denied(client, "vst_script_write", request({"op": "set_parameter", "id": pid, "value": value}))
            assert client.tool("file_list", {}, guard=False)["files"] == []
        finally:
            client.close()
        with settings(CANTABILE_VST3_SCRIPT_WRITE=None):
            client = Client(root / "opt-in-disabled")
            try:
                denied(client, "vst_script_write", request({"op": "set_parameter", "id": pid, "value": value}))
            finally:
                client.close()
        assert not list(root.rglob(".vst-*")), "A per-request staging directory survived cleanup"
    print("MCP control: Inspector JSON, parameter/state roundtrip, MIDI/WAV, permissions, failure isolation and persistent artifacts passed")


if __name__ == "__main__":
    main(*sys.argv[1:])
