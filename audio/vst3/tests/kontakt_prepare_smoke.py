"""Windows SDK + native dialog + guarded MCP contract, ORIGINAL SYNTHETIC plugin.

This does not run the vendor Lua engine and is not commercial acceptance.
"""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "host/tests"))
import test_host


def diagnose_owned_windows():
    # CI-only structural diagnostics: the synthetic worker's window classes,
    # numeric control IDs and ownership. Never titles, text, paths or other apps.
    import ctypes
    from ctypes import wintypes as w
    user = ctypes.WinDLL("user32")
    callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.EnumWindows.argtypes = [callback, w.LPARAM]
    user.EnumChildWindows.argtypes = [w.HWND, callback, w.LPARAM]
    user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user.GetWindowThreadProcessId.restype = w.DWORD
    user.GetAncestor.argtypes = [w.HWND, w.UINT]
    user.GetAncestor.restype = w.HWND
    user.GetDlgCtrlID.argtypes = [w.HWND]
    def name(window):
        buf = ctypes.create_unicode_buffer(128)
        user.GetClassNameW(window, buf, 128)
        return buf.value
    hosts = []
    @callback
    def find_host(window, _):
        if name(window) == "CantabileKontaktPreparation":
            process = w.DWORD()
            user.GetWindowThreadProcessId(window, ctypes.byref(process))
            hosts.append((window, process.value))
        return True
    user.EnumWindows(find_host, 0)
    for host, expected_process in hosts:
        windows = []
        @callback
        def collect(window, _):
            process = w.DWORD()
            thread = user.GetWindowThreadProcessId(window, ctypes.byref(process))
            if process.value != expected_process:
                return True
            children = []
            @callback
            def child(control, _):
                children.append(dict(kind=name(control), id=user.GetDlgCtrlID(control)))
                return len(children) < 100
            user.EnumChildWindows(window, child, 0)
            windows.append(dict(kind=name(window), thread=thread, owned=user.GetAncestor(window, 3) == host, children=children))
            return True
        user.EnumWindows(collect, 0)
        print("SYNTHETIC WORKER WINDOW STRUCTURE:", json.dumps(windows), flush=True)


def wait(client, job):
    deadline = time.monotonic() + 140
    diagnostic_at = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.tool("vst_job_status", {"job_id": job["job_id"]})
        if result["state"] not in ("queued", "running"):
            return result
        if time.monotonic() > diagnostic_at:
            diagnose_owned_windows()
            diagnostic_at = float("inf")
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
        (library / "Diagnostics.nki").write_text("original synthetic noisy stderr")
        config = root / "libraries.json"
        config.write_text(json.dumps({"version": 1, "libraries": [{"id": "original", "name": "Synthetic", "root": str(library)}]}))
        os.environ.update(CANTABILE_KONTAKT_LIBRARIES=str(config), CANTABILE_KONTAKT_PREPARE="true",
                          CANTABILE_VST3_PATHS=fixture, CANTABILE_FIXTURE_KONTAKT_KEYS="native-focus",
                          CANTABILE_KONTAKT_DIALOG_WAIT_SECONDS="30")
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
            diagnostics = next(x for x in items if x["display_name"] == "Diagnostics.nki")
            before = client.tool("project_get", {})
            request = dict(catalog_id=target["catalog_id"], library_id="original", instrument_id=original["content_id"], snapshot_id=snapshot["content_id"])
            job = wait(client, client.tool("kontakt_prepare_begin", request))
            if job["state"] != "completed":
                # Reproduce only this synthetic CLI failure with bounded direct
                # supervision, so fixture diagnostics do not enter production MCP.
                debug = root / "direct-debug"
                debug.mkdir()
                (debug / "prepare.json").write_text(json.dumps(dict(version=1, nonce="d"*32,
                    instrument=str(library / "Original '声' [1].nki"), snapshot=str(library / "Variant.nksn"))))
                try:
                    direct = subprocess.run([worker, "kontakt-prepare", "--plugin", fixture, "--class-id", target["class_id"],
                        "--script", str(debug / "prepare.json"), "--workspace", str(debug), "--output", str(debug / "prepared.vstpreset"),
                        "--max-seconds", "20", "--allow-write"], cwd=debug, capture_output=True, text=True, timeout=25)
                    print("SYNTHETIC CLI DIAGNOSTIC:", direct.stdout, direct.stderr, flush=True)
                except subprocess.TimeoutExpired:
                    print("SYNTHETIC CLI DIAGNOSTIC: bounded timeout", flush=True)
            assert job["state"] == "completed", job
            result = job["result"]
            assert result["state_captured"] and not result["audible_verified"] and not result["project_changed"]
            assert result["dialog_wait_seconds"] == 30, result
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
            noisy = wait(client, client.tool("kontakt_prepare_begin", dict(no_snapshot, instrument_id=diagnostics["content_id"])))
            assert noisy["state"] == "completed" and noisy["result"]["state_captured"], noisy
            assert "synthetic noisy stderr" not in json.dumps(noisy), noisy
            assert client.download(noisy["result"]["artifact"]["file_id"], chunk=128000)[:4] == b"VST3"
            assert client.tool("project_get", {}) == before
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
            assert failure["error"]["dialog_wait_seconds"] == 30, failure
            assert client.tool("file_list", {}) == files
            assert client.tool("project_get", {}) == before
            pending = client.tool("kontakt_prepare_begin", request)
            client.tool("vst_job_cancel", {"job_id": pending["job_id"]})
            cancelled = wait(client, pending)
            assert cancelled["state"] == "cancelled", cancelled
            assert client.tool("file_list", {}) == files
            assert client.tool("project_get", {}) == before
            # SDK-only, SDK returning false after opening, absent/ambiguous
            # native children, and an editor that handles neither route.
            # Every attempt is a fresh subprocess with a bounded deadline.
            cases = [(mode, None) for mode in ("sdk", "sdk-focus", "sdk-false", "native", "native-nested-focus", "native-parent-focus", "missing", "ambiguous")]
            cases += [("sdk", 90), ("sdk-delayed", 30), ("none", 1)]
            for mode, dialog_wait in cases:
                attempt = root / ("keys-" + mode + "-" + str(dialog_wait))
                attempt.mkdir()
                (attempt / "prepare.json").write_text(json.dumps(dict(version=1, nonce="e"*32,
                    instrument=str(library / "Original '声' [1].nki"), snapshot=None)))
                began = time.monotonic()
                direct = subprocess.run([worker, "kontakt-prepare", "--plugin", fixture,
                    "--class-id", target["class_id"], "--script", str(attempt / "prepare.json"),
                    "--workspace", str(attempt), "--output", str(attempt / "prepared.vstpreset"),
                    "--max-seconds", "40", "--allow-write"] +
                    (["--dialog-wait-seconds", str(dialog_wait)] if dialog_wait is not None else []), cwd=attempt, capture_output=True,
                    text=True, timeout=45, env=dict(os.environ, CANTABILE_FIXTURE_KONTAKT_KEYS=mode))
                published = attempt / "prepared.vstpreset"
                assert "synthetic_duplicate_f11" not in direct.stderr, (mode, direct.stderr)
                assert "synthetic_f11_unfocused" not in direct.stderr, (mode, direct.stderr)
                if mode in ("sdk", "sdk-focus", "sdk-false", "sdk-delayed", "native", "native-nested-focus", "native-parent-focus"):
                    assert direct.returncode == 0 and published.exists(), (mode, direct.stdout, direct.stderr)
                    assert json.loads(direct.stdout)["dialog_wait_seconds"] == (dialog_wait if dialog_wait is not None else 20), direct.stdout
                    if mode == "sdk-delayed":
                        assert time.monotonic() - began >= 24 and "synthetic_delayed_dialog=true" in direct.stderr, direct.stderr
                    if mode.startswith("sdk"):
                        assert "synthetic_native_f11" not in direct.stderr, (mode, direct.stderr)
                    else:
                        assert "synthetic_native_f11=true" in direct.stderr, (mode, direct.stderr)
                    if mode == "native-parent-focus":
                        assert "synthetic_parent_focus_forwarded=true" in direct.stderr, direct.stderr
                else:
                    assert direct.returncode != 0 and not published.exists(), (mode, direct.stdout, direct.stderr)
                    expected = "KONTAKT_LUA_UNAVAILABLE" if mode == "none" else "KONTAKT_EDITOR_UNSUPPORTED"
                    assert expected in direct.stdout, (mode, direct.stdout, direct.stderr)
            assert client.tool("file_list", {}) == files
            assert client.tool("project_get", {}) == before
            for value in ("0", "91", "1.5", ""):
                invalid = subprocess.run([worker, "kontakt-prepare", "--plugin", fixture,
                    "--dialog-wait-seconds", value], capture_output=True, text=True, timeout=10)
                assert invalid.returncode != 0 and "--dialog-wait-seconds must be an integer from 1 to 90" in invalid.stdout, invalid.stdout
            misplaced = subprocess.run([worker, "list", "--plugin", fixture, "--dialog-wait-seconds", "30"],
                capture_output=True, text=True, timeout=10)
            assert misplaced.returncode != 0 and "INVALID_ARGUMENT" in misplaced.stdout, misplaced.stdout
            for index, value in enumerate(("0", "91", "invalid", "")):
                os.environ["CANTABILE_KONTAKT_DIALOG_WAIT_SECONDS"] = value
                invalid_client = test_host.Client(root / ("invalid-config-" + str(index)))
                try:
                    unchanged = invalid_client.tool("project_get", {})
                    unchanged_files = invalid_client.tool("file_list", {})
                    rejected = invalid_client.tool_result("kontakt_prepare_begin", request)
                    assert rejected.get("isError") and "kontakt_dialog_wait_invalid" in json.dumps(rejected), rejected
                    assert invalid_client.tool("project_get", {}) == unchanged
                    assert invalid_client.tool("file_list", {}) == unchanged_files
                finally:
                    invalid_client.close()
            os.environ["CANTABILE_KONTAKT_DIALOG_WAIT_SECONDS"] = "30"
        finally:
            client.close()
    print("PASS: original SDK/editor/dialog/MCP fixture; vendor Lua and commercial libraries remain unverified")


if __name__ == "__main__":
    main()
