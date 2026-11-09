"""Independent black-box tests of the native C ABI and standard MIDI interchange.

Build first, then run ``python3 -m unittest discover -s tests -v``.
Set CANTABILE_LIBRARY (or CANTABILE_CORE) to override the shared library location. Optional mido adds
an independent third-party parser check; the other tests use stdlib only.
"""

import base64
import copy
import ctypes
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import struct
import threading
import unittest

try:
    import mido
except ImportError:
    mido = None


ROOT = Path(__file__).resolve().parents[1]


def vlq(value):
    if not 0 <= value <= 0x0FFFFFFF:
        raise ValueError(value)
    result = [value & 0x7F]
    value >>= 7
    while value:
        result.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(result)


def event(delta, payload):
    return vlq(delta) + bytes(payload)


def meta(delta, subtype, data):
    data = bytes(data)
    return vlq(delta) + bytes([0xFF, subtype]) + vlq(len(data)) + data


def smf(tracks, fmt=1, ppq=480):
    result = b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), ppq)
    for track in tracks:
        result += b"MTrk" + struct.pack(">I", len(track)) + track
    return result


def parse_smf(data):
    """Small independent parser returning absolute-tick channel/meta/SysEx events.

    It does not use Cantabile's schema or codec. Running status is expanded, so
    compressed and uncompressed SMFs compare by actual event meaning.
    """
    data = bytes(data)
    if data[:4] != b"MThd":
        raise ValueError("missing header")
    size, fmt, count, ppq = struct.unpack(">IHHH", data[4:14])
    offset = 8 + size
    parsed = []
    for _ in range(count):
        if data[offset:offset + 4] != b"MTrk":
            raise ValueError("missing track")
        length = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        body = data[offset + 8:offset + 8 + length]
        if len(body) != length:
            raise ValueError("short track")
        offset += 8 + length
        p = 0
        tick = 0
        running = None
        result = []

        def read_vlq():
            nonlocal p
            value = 0
            for _ in range(4):
                byte = body[p]
                p += 1
                value = (value << 7) | (byte & 0x7F)
                if not byte & 0x80:
                    return value
            raise ValueError("oversized VLQ")

        while p < len(body):
            tick += read_vlq()
            status = body[p]
            if status < 0x80:
                if running is None:
                    raise ValueError("missing running status")
                status = running
            else:
                p += 1
            if 0x80 <= status <= 0xEF:
                running = status
                length = 1 if status >> 4 in (0xC, 0xD) else 2
                payload = body[p:p + length]
                if len(payload) != length or any(v > 127 for v in payload):
                    raise ValueError("invalid channel payload")
                p += length
                result.append((tick, "midi", status, bytes(payload)))
            elif status == 0xFF:
                subtype = body[p]
                p += 1
                length = read_vlq()
                payload = body[p:p + length]
                p += length
                result.append((tick, "meta", subtype, bytes(payload)))
            elif status in (0xF0, 0xF7):
                running = None
                length = read_vlq()
                payload = body[p:p + length]
                p += length
                result.append((tick, "sysex", status, bytes(payload)))
            else:
                raise ValueError("unsupported status")
        parsed.append(result)
    if offset != len(data):
        raise ValueError("trailing file data")
    return fmt, ppq, parsed


def without_eot(tracks):
    return [[e for e in track if not (e[1] == "meta" and e[2] == 0x2F)]
            for track in tracks]


def score_fixture():
    conductor = (
        meta(0, 3, b"Conductor")
        + meta(0, 0x51, [7, 0xA1, 0x20])
        + meta(0, 0x58, [4, 2, 24, 8])
        + meta(0, 0x59, [0xFE, 1])
        + meta(120, 5, "かえりみち".encode("utf-8"))
        + meta(360, 0x51, [0x0A, 0x2C, 0x2B])
        + meta(0, 6, b"Second section")
        + meta(480, 0x7E, [0, 128, 255, 17])
        + meta(0, 0x2F, [])
    )
    music = (
        meta(0, 3, b"Lead")
        + event(0, [0xB2, 0, 1])
        + event(0, [0xB2, 32, 4])
        + event(0, [0xC2, 5])
        + event(0, [0xD2, 60])
        + event(0, [0xA2, 60, 42])
        + event(0, [0xE2, 0, 64])
        + event(0, [0xB2, 64, 127])
        + event(0, [0x92, 60, 100])
        + event(120, [0x92, 64, 80])
        + event(120, [0x82, 60, 29])
        + event(240, [0x92, 64, 0])
        + event(0, [0xB2, 64, 0])
        + event(0, [0xF0, 4, 0x7D, 1, 2, 0xF7])
        + event(0, [0xF7, 3, 0x41, 0x42, 0x43])
        + meta(480, 0x2F, [])
    )
    return smf([conductor, music])


def library_path():
    configured = os.environ.get("CANTABILE_LIBRARY") or os.environ.get("CANTABILE_CORE")
    if configured:
        return Path(configured)
    names = ["libcantabile_core.so", "libcantabile_core.dylib", "cantabile_core.dll"]
    for directory in (ROOT / "build", ROOT / "build" / "Release",
                      ROOT / "build" / "Debug"):
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    raise RuntimeError("Build cantabile_core first or set CANTABILE_LIBRARY")


class NativeInteropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = ctypes.CDLL(str(library_path()))
        cls.lib.cantabile_create.argtypes = []
        cls.lib.cantabile_create.restype = ctypes.c_void_p
        cls.lib.cantabile_destroy.argtypes = [ctypes.c_void_p]
        cls.lib.cantabile_request.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cls.lib.cantabile_request.restype = ctypes.c_void_p
        cls.lib.cantabile_free.argtypes = [ctypes.c_void_p]

    def setUp(self):
        self.engine = self.lib.cantabile_create()
        self.assertTrue(self.engine)
        self.revision = 0

    def tearDown(self):
        self.lib.cantabile_destroy(self.engine)

    def raw(self, request):
        pointer = self.lib.cantabile_request(self.engine, request)
        self.assertTrue(pointer, "ABI must return an owned JSON error, not null")
        try:
            return json.loads(ctypes.string_at(pointer).decode("utf-8"))
        finally:
            self.lib.cantabile_free(pointer)

    def call(self, op, mutate=False, **args):
        request = {"op": op, **args}
        if mutate:
            request.setdefault("expected_revision", self.revision)
        reply = self.raw(json.dumps(request, ensure_ascii=False).encode("utf-8"))
        if reply.get("ok") and "revision" in reply:
            self.revision = reply["revision"]
        return reply

    def good(self, result):
        self.assertTrue(result.get("ok"), result)
        return result

    def project(self):
        return self.good(self.call("project.get"))["project"]

    def imported(self, data):
        return self.good(self.call("midi.import", mutate=True,
                                  data_base64=base64.b64encode(data).decode()))

    def exported(self, **kwargs):
        result = self.good(self.call("midi.export", **kwargs))
        return base64.b64decode(result["data_base64"], validate=True)

    def loaded(self, project):
        return self.good(self.call("project.load", mutate=True, data=project))

    def edit(self, *commands):
        return self.call("edit.batch", mutate=True, commands=list(commands))

    def add_note_track(self):
        return self.good(self.edit({"op": "track.add", "track": {
            "id": "lead", "name": "Lead", "channel": 2,
            "events": [{"id": "n1", "tick": 13, "kind": "note", "pitch": 60,
                        "duration": 479, "velocity": 101, "release_velocity": 27,
                        "channel": 2}]}}))

    def test_initial_project_has_version_and_exact_timebase(self):
        project = self.project()
        self.assertEqual(project["schema_version"], 1)
        self.assertGreater(project["ppq"], 0)
        self.assertIsInstance(project["tracks"], list)

    def test_broad_smf_events_are_preserved(self):
        source = score_fixture()
        self.imported(source)
        fmt, ppq, tracks = parse_smf(self.exported())
        original = parse_smf(source)
        self.assertEqual((fmt, ppq), original[:2])
        self.assertEqual(without_eot(tracks), without_eot(original[2]))
        self.assertEqual([t[-1][0] for t in tracks], [960, 960])

    @unittest.skipIf(mido is None, "install mido for third-party parser verification")
    def test_mido_reads_exported_tempo_meter_and_channel_events(self):
        source = score_fixture()
        self.imported(source)
        original = mido.MidiFile(file=io.BytesIO(source))
        exported = mido.MidiFile(file=io.BytesIO(self.exported()))
        self.assertEqual(exported.ticks_per_beat, original.ticks_per_beat)
        self.assertEqual(exported.type, original.type)
        self.assertEqual([[m.dict() for m in t] for t in exported.tracks],
                         [[m.dict() for m in t] for t in original.tracks])

    def test_running_status_expands_without_changing_absolute_ticks(self):
        track = (meta(0, 3, b"Running") + event(0, [0x90, 60, 100])
                 + event(128, [64, 90]) + event(127, [67, 80])
                 + event(129, [60, 0]) + event(0, [64, 0])
                 + event(0, [67, 0]) + meta(256, 0x2F, []))
        source = smf([track], fmt=0, ppq=960)
        self.imported(source)
        exported = self.exported(format=0)
        self.assertEqual(parse_smf(exported), parse_smf(source))

    def test_overlapping_same_pitch_and_zero_velocity_note_on_survive(self):
        track = (meta(0, 3, b"Overlap") + event(0, [0x90, 60, 100])
                 + event(10, [0x90, 60, 70]) + event(10, [0x90, 60, 0])
                 + event(0, [0x80, 60, 37]) + meta(0, 0x2F, []))
        source = smf([track], fmt=0)
        self.imported(source)
        self.assertEqual(parse_smf(self.exported(format=0)), parse_smf(source))

    def test_project_json_reload_preserves_ids_opaque_data_and_instruments(self):
        self.imported(score_fixture())
        project = self.project()
        project["tracks"][0]["instrument"] = {
            "backend": "vst3", "class_id": "test-only-missing", "state_base64": "AAH/"}
        self.loaded(project)
        saved = json.loads(json.dumps(self.project(), ensure_ascii=False))
        self.loaded(saved)
        self.assertEqual(self.project(), saved)
        self.assertFalse(self.call("project.get")["can_undo"])

    def test_renaming_imported_track_changes_exported_track_name(self):
        self.imported(score_fixture())
        track_id = self.project()["tracks"][1]["id"]
        self.good(self.edit({"op": "track.update", "track_id": track_id,
                             "patch": {"name": "Renamed lead"}}))
        self.assertEqual(self.project()["tracks"][1]["name"], "Renamed lead")
        track = parse_smf(self.exported())[2][1]
        names = [e[3] for e in track if e[1] == "meta" and e[2] == 3]
        self.assertEqual(names, [b"Renamed lead"])

    def test_invalid_midi_cannot_replace_a_valid_project(self):
        self.imported(score_fixture())
        before = self.project()
        revision = self.revision
        valid = smf([meta(0, 0x2F, [])], fmt=0)
        malformed = [
            b"", b"not midi", valid[:12], valid[:-1],
            smf([b"\x00\x90\x40"], fmt=0),
            smf([b"\x00\x40\x64"], fmt=0),
            smf([b"\x81\x81\x81\x81\x00\xFF\x2F\x00"], fmt=0),
            smf([event(0, [0x90, 60, 128]) + meta(1, 0x2F, [])], fmt=0),
            smf([b"\x00\xFF\x7E\x08\x01"], fmt=0),
            smf([meta(0, 0x2F, [])], fmt=2),
            smf([meta(0, 0x2F, [])], fmt=0, ppq=0xE728),
        ]
        for data in malformed:
            with self.subTest(data=data.hex()):
                result = self.call("midi.import", mutate=True,
                                   data_base64=base64.b64encode(data).decode())
                self.assertFalse(result["ok"], result)
                self.assertEqual(self.revision, revision)
                self.assertEqual(self.project(), before)

    def test_base64_is_validated_before_import(self):
        before = self.project()
        for value in ["%%%", "AAA", "AA=A", "=AAA", "YWJj!!!!"]:
            with self.subTest(value=value):
                self.assertFalse(self.call("midi.import", mutate=True,
                                          data_base64=value)["ok"])
                self.assertEqual(self.project(), before)

    def test_invalid_batch_is_atomic_including_history_and_revision(self):
        before = self.project()
        revision = self.revision
        result = self.edit(
            {"op": "track.add", "track": {"id": "valid", "name": "Good", "events": []}},
            {"op": "event.add", "track_id": "valid", "event": {
                "id": "bad", "kind": "note", "tick": 0, "pitch": 60,
                "duration": 480, "velocity": 128, "channel": 0}})
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.revision, revision)
        state = self.good(self.call("project.get"))
        self.assertEqual(state["project"], before)
        self.assertFalse(state["can_undo"])

    def test_mutation_requires_correct_revision(self):
        self.add_note_track()
        before = self.project()
        revision = self.revision
        command = [{"op": "project.update", "patch": {"name": "Stale"}}]
        for req in [{"op": "edit.batch", "commands": command},
                    {"op": "edit.batch", "commands": command,
                     "expected_revision": revision - 1}]:
            result = self.raw(json.dumps(req).encode())
            self.assertFalse(result["ok"], result)
            self.assertEqual(self.project(), before)
            self.assertEqual(self.revision, revision)

    def test_undo_redo_and_edit_branch_keep_revision_monotonic(self):
        initial = self.project()
        self.add_note_track()
        edited = self.project()
        first = self.revision
        self.good(self.call("history.undo", mutate=True))
        self.assertGreater(self.revision, first)
        self.assertEqual(self.project(), initial)
        second = self.revision
        self.good(self.call("history.redo", mutate=True))
        self.assertGreater(self.revision, second)
        self.assertEqual(self.project(), edited)
        self.good(self.call("history.undo", mutate=True))
        self.good(self.edit({"op": "project.update", "patch": {"name": "New branch"}}))
        self.assertFalse(self.good(self.call("project.get"))["can_redo"])
        self.assertFalse(self.call("history.redo", mutate=True)["ok"])

    def test_typed_note_exports_exact_pitch_duration_and_release_velocity(self):
        self.add_note_track()
        _, _, tracks = parse_smf(self.exported())
        events = [e for track in tracks for e in track if e[1] == "midi"]
        self.assertIn((13, "midi", 0x92, bytes([60, 101])), events)
        self.assertIn((492, "midi", 0x82, bytes([60, 27])), events)

    def test_playback_mute_preserves_composed_song_duration_without_mutation(self):
        self.add_note_track()
        self.good(self.edit({"op": "track.update", "track_id": "lead",
                             "patch": {"muted": True}}))
        before = self.project()
        _, _, tracks = parse_smf(self.exported(playback=True))
        self.assertFalse([e for t in tracks for e in t if e[1] == "midi"])
        self.assertEqual(max(t[-1][0] for t in tracks), 492)
        self.assertEqual(self.project(), before)
        _, _, ordinary = parse_smf(self.exported())
        self.assertTrue([e for t in ordinary for e in t if e[1] == "midi"])

    def test_playback_solo_keeps_conductor_metadata_and_omits_other_tracks(self):
        self.imported(score_fixture())
        self.good(self.edit({"op": "track.add", "track": {
            "id": "solo", "name": "Solo", "channel": 3, "solo": True,
            "events": [{"id": "solo-note", "tick": 0, "kind": "note",
                        "pitch": 72, "duration": 480, "velocity": 90,
                        "channel": 3}]}}))
        before = self.project()
        _, _, tracks = parse_smf(self.exported(playback=True))
        channel_events = [e for t in tracks for e in t if e[1] == "midi"]
        self.assertEqual([e[2] for e in channel_events], [0x93, 0x83])
        self.assertEqual([e for e in tracks[0] if e[1] == "meta" and e[2] == 0x51],
                         [e for e in parse_smf(score_fixture())[2][0]
                          if e[1] == "meta" and e[2] == 0x51])
        self.assertEqual(self.project(), before)

    def test_transpose_and_quantize_change_notes_without_touching_controllers(self):
        self.add_note_track()
        self.good(self.edit({"op": "event.add", "track_id": "lead", "event": {
            "id": "cc", "kind": "midi", "tick": 13, "status": 0xB2,
            "data": [64, 127]}}))
        self.good(self.edit(
            {"op": "events.transpose", "track_id": "lead", "semitones": 7},
            {"op": "events.quantize", "track_id": "lead", "grid": 120}))
        events = {e["id"]: e for e in self.project()["tracks"][-1]["events"]}
        self.assertEqual(events["n1"]["pitch"], 67)
        self.assertEqual(events["n1"]["tick"], 0)
        self.assertEqual(events["cc"]["status"], 0xB2)
        self.assertEqual(events["cc"]["data"], [64, 127])

    def test_bad_project_values_are_rejected_transactionally(self):
        self.add_note_track()
        original = self.project()
        invalid = []
        for field, value in [("ppq", 0), ("ppq", 0x8000), ("schema_version", 2)]:
            bad = copy.deepcopy(original)
            bad[field] = value
            invalid.append(bad)
        for field, value in [("tick", -1), ("duration", 0), ("pitch", 128),
                             ("velocity", 0), ("channel", 16)]:
            bad = copy.deepcopy(original)
            bad["tracks"][-1]["events"][0][field] = value
            invalid.append(bad)
        for bad in invalid:
            with self.subTest(bad=bad):
                self.assertFalse(self.call("project.load", mutate=True, data=bad)["ok"])
                self.assertEqual(self.project(), original)

    def test_duplicate_stable_ids_are_rejected(self):
        self.add_note_track()
        original = self.project()
        bad = copy.deepcopy(original)
        bad["tracks"][-1]["events"].append(copy.deepcopy(bad["tracks"][-1]["events"][0]))
        self.assertFalse(self.call("project.load", mutate=True, data=bad)["ok"])
        self.assertEqual(self.project(), original)

    def test_caller_byte_budget_rejects_utf8_edit_without_consuming_history(self):
        self.add_note_track()
        self.good(self.edit({"op": "project.update", "patch": {"name": "small"}}))
        self.good(self.call("history.undo", mutate=True))
        before = self.good(self.call("project.get"))
        self.assertTrue(before["can_redo"])
        # 400 characters fit a character-count limit of 1024; UTF-8 requires
        # 1200 bytes before accounting for the rest of the serialized project.
        result = self.call("edit.batch", mutate=True, max_project_bytes=1024,
                           commands=[{"op": "project.update", "patch": {"name": "あ" * 400}}])
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], "LIMIT_EXCEEDED")
        self.assertEqual(self.good(self.call("project.get")), before)

    def test_caller_budget_checks_load_import_and_new_before_replacing_project(self):
        self.add_note_track()
        before = self.good(self.call("project.get"))
        requests = [
            ("project.load", {"data": before["project"]}),
            ("midi.import", {"data_base64": base64.b64encode(score_fixture()).decode()}),
            ("project.new", {}),
        ]
        for op, fields in requests:
            with self.subTest(op=op):
                result = self.call(op, mutate=True, max_project_bytes=1, **fields)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["error"]["code"], "LIMIT_EXCEEDED")
                self.assertEqual(self.good(self.call("project.get")), before)

    def test_caller_budget_failed_undo_and_redo_leave_entries_available(self):
        self.add_note_track()
        self.good(self.edit({"op": "project.update", "patch": {"name": "x" * 1200}}))
        big = self.project()
        self.good(self.edit({"op": "project.update", "patch": {"name": "small"}}))
        before_undo = self.good(self.call("project.get"))
        failed = self.call("history.undo", mutate=True, max_project_bytes=1024)
        self.assertFalse(failed["ok"], failed)
        self.assertEqual(failed["error"]["code"], "LIMIT_EXCEEDED")
        self.assertEqual(self.good(self.call("project.get")), before_undo)
        self.good(self.call("history.undo", mutate=True, max_project_bytes=4096))
        self.assertEqual(self.project(), big)
        before_redo = self.good(self.call("project.get"))
        failed = self.call("history.redo", mutate=True, max_project_bytes=1)
        self.assertFalse(failed["ok"], failed)
        self.assertEqual(failed["error"]["code"], "LIMIT_EXCEEDED")
        self.assertEqual(self.good(self.call("project.get")), before_redo)
        self.good(self.call("history.redo", mutate=True, max_project_bytes=4096))
        self.assertEqual(self.project(), before_undo["project"])

    def test_caller_budget_must_be_bounded_positive_integer(self):
        before = self.good(self.call("project.get"))
        for value in [0, -1, 32 * 1024 * 1024 + 1, 1024.5, "1024"]:
            with self.subTest(value=value):
                result = self.call("project.new", mutate=True, max_project_bytes=value)
                self.assertFalse(result["ok"], result)
                self.assertEqual(self.good(self.call("project.get")), before)

    def test_invalid_requests_return_json_and_do_not_break_next_call(self):
        for request in [b"{", b"null", b"[]", b"42", b'{"op":7}', b'{"op":"unknown"}']:
            with self.subTest(request=request):
                result = self.raw(request)
                self.assertFalse(result["ok"], result)
                self.assertIsInstance(result["error"]["message"], str)
                self.assertEqual(self.project()["schema_version"], 1)

    def test_abi_null_arguments_return_owned_errors(self):
        for engine, request in [(None, b'{"op":"project.get"}'),
                                (self.engine, None)]:
            pointer = self.lib.cantabile_request(engine, request)
            self.assertTrue(pointer)
            try:
                result = json.loads(ctypes.string_at(pointer))
                self.assertFalse(result["ok"], result)
            finally:
                self.lib.cantabile_free(pointer)
        self.lib.cantabile_free(None)
        self.lib.cantabile_destroy(None)

    def test_concurrent_same_revision_edits_have_one_winner(self):
        barrier = threading.Barrier(8)

        def attempt(index):
            request = json.dumps({"op": "edit.batch", "expected_revision": 0,
                                  "commands": [{"op": "project.update",
                                                "patch": {"name": str(index)}}]}).encode()
            barrier.wait(timeout=10)
            return self.raw(request)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(result["ok"] for result in results), 1, results)
        state = self.good(self.call("project.get"))
        self.assertEqual(state["revision"], 1)
        self.assertTrue(state["can_undo"])


if __name__ == "__main__":
    unittest.main()
