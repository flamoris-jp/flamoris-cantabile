"""Windows SDK + native dialog + guarded MCP contract, ORIGINAL SYNTHETIC plugin.

This does not run the vendor Lua engine and is not commercial acceptance.
"""
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "host/tests"))
import test_host


def wait(client, job):
    deadline = time.monotonic() + 140
    while time.monotonic() < deadline:
        result = client.tool("vst_job_status", {"job_id": job["job_id"]})
        if result["state"] not in ("queued", "running"):
            return result
        time.sleep(.05)
    raise AssertionError("bounded job failed to terminate")


def main():
    assert os.name == "nt", "Windows contract gate must not be counted on Linux"
    worker, fixture, core = map(lambda p: str(Path(p).resolve()), sys.argv[1:4])
    test_host.CORE = Path(core)
    test_host.RENDERER = Path(worker)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        library = root / "Original '日本語' library"
        library.mkdir()
        (library / "Original '声' [1].nki").write_text("original synthetic ready")
        (library / "Failure.nki").write_text("original synthetic failure")
        (library / "Variant.nksn").write_text("synthetic snapshot")
        config = root / "libraries.json"
        config.write_text(json.dumps({"version": 1, "libraries": [{"id": "original", "name": "Synthetic", "root": str(library)}]}))
        os.environ.update(CANTABILE_KONTAKT_LIBRARIES=str(config), CANTABILE_KONTAKT_PREPARE="true", CANTABILE_VST3_PATHS=fixture)
        client = test_host.Client(root / "workspace")
        try:
            scan = wait(client, client.tool("vst_scan", {}))
            assert scan["state"] == "completed", scan
            catalog = client.tool("instruments_list", {})["catalog"]["instruments"]
            target = next(x for x in catalog if x["name"] == "Kontakt 8")
            assert target["usable"], target
            items = client.tool("kontakt_library_list", {"library_id": "original"})["items"]
            original = next(x for x in items if "Original" in x["display_name"])
            failed = next(x for x in items if x["display_name"] == "Failure.nki")
            snapshot = next(x for x in items if x["kind"] == "snapshot")
            before = client.tool("project_get", {})
            request = dict(catalog_id=target["catalog_id"], library_id="original", instrument_id=original["content_id"], snapshot_id=snapshot["content_id"])
            job = wait(client, client.tool("kontakt_prepare_begin", request))
            assert job["state"] == "completed", job
            result = job["result"]
            assert result["state_captured"] and not result["audible_verified"] and not result["project_changed"]
            assert client.tool("project_get", {}) == before
            state = client.download(result["artifact"]["file_id"], chunk=128000)
            assert state[:4] == b"VST3"
            directory = struct.unpack_from("<Q", state, 40)[0]
            count = struct.unpack_from("<I", state, directory + 4)[0]
            chunks = {}
            for i in range(count):
                pos = directory + 8 + 20*i
                name, offset, size = struct.unpack_from("<4sQQ", state, pos)
                chunks[name] = state[offset:offset+size]
            assert chunks[b"Comp"] == struct.pack("<i", 1), chunks
            no_snapshot = dict(request)
            del no_snapshot["snapshot_id"]
            plain = wait(client, client.tool("kontakt_prepare_begin", no_snapshot))
            assert plain["state"] == "completed" and plain["result"]["snapshot_id"] is None, plain
            client.tool("edit_batch", {"commands": [{"op": "track.add", "track": {"id": "original", "events": []}}]})
            client.tool("instrument_assign", {"track_id": "original", "catalog_id": target["catalog_id"]})
            base = client.tool("project_get", {})["project"]
            client.tool("instrument_state_set", {"track_id": "original", "file_id": result["artifact"]["file_id"]})
            applied = client.tool("project_get", {})["project"]
            assert "state_base64" in applied["tracks"][0]["instrument"]
            client.tool("history_undo", {})
            assert client.tool("project_get", {})["project"] == base
            client.tool("history_redo", {})
            assert client.tool("project_get", {})["project"] == applied
            client.tool("project_save", {"name": "original.cantabile.json"})
            client.tool("project_open", {"name": "original.cantabile.json", "discard_unsaved": False})
            assert client.tool("project_get", {})["project"] == applied
            before = client.tool("project_get", {})
            files = client.tool("file_list", {})
            failure = wait(client, client.tool("kontakt_prepare_begin", dict(request, instrument_id=failed["content_id"])))
            assert failure["state"] == "failed" and failure["error"]["code"] == "KONTAKT_LOAD_FAILED", failure
            assert client.tool("file_list", {}) == files
            assert client.tool("project_get", {}) == before
            pending = client.tool("kontakt_prepare_begin", request)
            client.tool("vst_job_cancel", {"job_id": pending["job_id"]})
            cancelled = wait(client, pending)
            assert cancelled["state"] == "cancelled", cancelled
            assert client.tool("file_list", {}) == files
            assert client.tool("project_get", {}) == before
        finally:
            client.close()
    print("PASS: original SDK/editor/dialog/MCP fixture; vendor Lua and commercial libraries remain unverified")


if __name__ == "__main__":
    main()
