"""Exercise MCP import, a real VST render, and verified MIDI/WAV retrieval."""
import base64
import io
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import wave

from smoke import invoke, midi_fixture


def main(worker, cli, plugin, core):
    classes = invoke(worker, "list", "--plugin", plugin)["instruments"]
    piano = next(c for c in classes if c["name"].lower() == "mda piano")
    os.environ.update(
        CANTABILE_RENDERER=worker, CANTABILE_CORE_LIBRARY=core,
        CANTABILE_VST3_MODULE=plugin, CANTABILE_VST3_CLASS_ID=piano["id"],
    )
    tests = Path(__file__).resolve().parents[3] / "host" / "tests"
    sys.path.insert(0, str(tests))
    from test_host import Client

    with TemporaryDirectory() as directory:
        root = Path(directory)
        midi = midi_fixture(cli, root).read_bytes()
        client = Client(root / "workspace")
        try:
            instruments = client.tool("instruments_list", {}, guard=False)
            assert instruments["active_backend"] == "vst3" and instruments["vst3"] == "configured", instruments
            client.tool("midi_import", {"data_base64": base64.b64encode(midi).decode(), "discard_unsaved": False})
            exported = client.tool("midi_export", {})
            assert client.download(exported["file_id"], chunk=128 * 1024) == midi
            job = client.tool("audio_render", {"max_seconds": 10})
            deadline = time.monotonic() + 20
            while job["state"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.1)
                job = client.tool("audio_status", {"job_id": job["job_id"]}, guard=False)
            assert job["state"] == "completed", job
            assert job["audio"]["backend"] == "vst3" and job["audio"]["peak"] > 0.01, job
            payload = client.download(job["artifact"]["file_id"], chunk=128 * 1024)
            with wave.open(io.BytesIO(payload)) as wav:
                assert (wav.getnchannels(), wav.getframerate(), wav.getnframes()) == (2, 48000, 307200)
        finally:
            client.close()
    print("MCP: imported MIDI, exact MIDI retrieval, real VST rendering, verified stereo WAV retrieval")


if __name__ == "__main__":
    main(*sys.argv[1:])
