"""Exercise MCP import, a real VST render, and verified MIDI/WAV retrieval."""
import base64
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import wave

from smoke import invoke, midi_fixture


@contextmanager
def configuration(realtime=None, warmup=None):
    settings = {"CANTABILE_VST3_REALTIME": realtime, "CANTABILE_VST3_WARMUP_MS": warmup}
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


def instruments(client, mode, warmup):
    result = client.tool("instruments_list", {}, guard=False)
    assert result["active_backend"] == "vst3" and result["vst3"] == "configured", result
    assert (result["process_mode"], result["warmup_ms"]) == (mode, warmup), result


def wait_terminal(client, job, timeout=20):
    deadline = time.monotonic() + timeout
    while job["state"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.05)
        job = client.tool("audio_status", {"job_id": job["job_id"]}, guard=False)
    assert job["state"] not in {"queued", "running"}, job
    return job


def render_case(Client, folder, midi, mode, warmup):
    client = Client(folder)
    try:
        instruments(client, mode, warmup)
        client.tool("midi_import", {"data_base64": base64.b64encode(midi).decode(), "discard_unsaved": False})
        exported = client.tool("midi_export", {})
        assert client.download(exported["file_id"], chunk=128 * 1024) == midi
        # Processing settings belong to the host, not arbitrary MCP request fields.
        for forbidden in ({"realtime": True}, {"warmup_ms": 300}):
            assert client.tool_result("audio_render", {"max_seconds": 10, **forbidden})["isError"]
        started = time.monotonic()
        job = wait_terminal(client, client.tool("audio_render", {"max_seconds": 10}))
        elapsed = time.monotonic() - started
        assert job["state"] == "completed", job
        audio = job["audio"]
        assert audio["backend"] == "vst3" and audio["peak"] > 0.01, job
        assert (audio["process_mode"], audio["warmup_ms"]) == (mode, warmup), audio
        if mode == "realtime":
            assert elapsed >= 6.6, (elapsed, job)
        payload = client.download(job["artifact"]["file_id"], chunk=128 * 1024)
        with wave.open(io.BytesIO(payload)) as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (2, 2, 48000, 307200)
            # A startup wait must not become leading silence or extend the WAV.
            assert any(wav.readframes(48000)), "The first note was lost or shifted."
        return payload
    finally:
        client.close()


def configuration_cases(Client, dotnet, host, root):
    # Inspect upper bounds without actually waiting thirty seconds.
    valid = [("False", "0", "offline", 0), ("0", "30000", "offline", 30000),
             ("TrUe", "1", "realtime", 1), ("1", "000", "realtime", 0),
             ("", "", "offline", 0)]
    for index, (realtime, warmup, mode, milliseconds) in enumerate(valid):
        with configuration(realtime, warmup):
            client = Client(root / f"valid-{index}")
            try:
                instruments(client, mode, milliseconds)
            finally:
                client.close()
    invalid = [("yes", "0"), ("2", "0"), (" true", "0"), ("true ", "0"),
               ("false", "-1"), ("false", "30001"), ("false", "1.5"),
               ("false", "+1"), ("false", " 1"), ("false", "2147483648")]
    for index, (realtime, warmup) in enumerate(invalid):
        with configuration(realtime, warmup):
            workspace = root / f"invalid-{index}"
            result = subprocess.run([dotnet, str(host), "catalog", "--workspace", str(workspace)],
                                    text=True, capture_output=True, encoding="utf-8", timeout=15)
            assert result.returncode != 0 and not result.stdout.strip(), (result.stdout, result.stderr)
            error = json.loads(result.stderr.strip().splitlines()[-1])
            assert error == {"ok": False, "error": {"code": "invalid_configuration"}}, error
            assert not list(workspace.rglob("*.wav")), workspace


def cancellation_case(Client, folder, midi):
    with configuration("true", "30000"):
        client = Client(folder)
        try:
            client.tool("midi_import", {"data_base64": base64.b64encode(midi).decode(), "discard_unsaved": False})
            job = client.tool("audio_render", {"max_seconds": 10})
            # The real worker should still be alive in its configured startup wait.
            time.sleep(1)
            job = client.tool("audio_status", {"job_id": job["job_id"]}, guard=False)
            assert job["state"] == "running", job
            started = time.monotonic()
            job = wait_terminal(client, client.tool("audio_cancel", {"job_id": job["job_id"]}), timeout=5)
            assert time.monotonic() - started < 5, job
            assert job["state"] == "cancelled" and job["artifact"] is None, job
            assert client.tool("file_list", {}, guard=False)["files"] == []
        finally:
            client.close()
        # Closing joins the render task, including its temporary-file cleanup.
        assert not list(folder.rglob("*.wav")) and not list(folder.rglob("*.part"))


def main(worker, cli, plugin, core):
    classes = invoke(worker, "list", "--plugin", plugin)["instruments"]
    piano = next(c for c in classes if c["name"].lower() == "mda piano")
    os.environ.update(
        CANTABILE_RENDERER=worker, CANTABILE_CORE_LIBRARY=core,
        CANTABILE_VST3_MODULE=plugin, CANTABILE_VST3_CLASS_ID=piano["id"],
    )
    os.environ.pop("CANTABILE_VST3_PRESET", None)
    tests = Path(__file__).resolve().parents[3] / "host" / "tests"
    sys.path.insert(0, str(tests))
    from test_host import Client, DOTNET, HOST

    with TemporaryDirectory() as directory:
        root = Path(directory)
        midi = midi_fixture(cli, root).read_bytes()
        with configuration():
            offline = render_case(Client, root / "offline", midi, "offline", 0)
        with configuration("true", "300"):
            realtime = render_case(Client, root / "realtime", midi, "realtime", 300)
        assert offline == realtime, "Processing pace and startup wait changed the mda Piano PCM."
        configuration_cases(Client, DOTNET, HOST, root)
        cancellation_case(Client, root / "cancelled", midi)
    print("MCP: offline/realtime VST renders, identical PCM and exact MIDI, validated host configuration, prompt cancellation without WAV publication")


if __name__ == "__main__":
    main(*sys.argv[1:])
