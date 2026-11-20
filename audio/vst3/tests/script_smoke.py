"""Phase 2 acceptance against a real SDK instrument, after the Inspector gate.

The DSL has no interpreter, imports, OS commands, or user-selected executable.
All MIDI/WAV/state fixtures are generated temporarily and never tracked by Git.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic
import wave

from inspector_smoke import inspect_piano, invoke
from smoke import midi_fixture


def main(worker, cli, plugin):
    # Independent execution remains safe if this script is invoked without CI.
    piano, inspection = inspect_piano(worker, plugin)
    parameter = next(p for p in inspection["parameters"] if not p["flags"] & 2 and p["step_count"] == 0)
    identifier, original = parameter["id"], parameter["value"]
    changed = 0.8 if original < 0.6 else 0.2
    with TemporaryDirectory() as directory:
        folder = Path(directory) / "操作"
        folder.mkdir()
        # The existing MIDI CLI accepts narrow argv on Windows. Generate the
        # fixture outside the Unicode directory, then test the wide-path VST
        # worker with the copied MIDI, as the original render smoke does.
        fixture = midi_fixture(cli, folder.parent)
        midi = folder / "piano.mid"
        midi.write_bytes(fixture.read_bytes())
        midi_digest = hashlib.sha256(midi.read_bytes()).digest()
        request = folder / "request.json"
        arguments = ["--plugin", plugin, "--class-id", piano["id"]]

        def script(operations, *, write=False, expected_error=None, limits=None, extra=(), document_extra=None):
            document = {"version": 1, "operations": operations}
            if limits is not None:
                document["limits"] = limits
            document.update(document_extra or {})
            request.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            return invoke(worker, "script", *arguments, "--script", request, "--workspace", folder,
                          *(["--allow-write"] if write else []), *extra, expected_error=expected_error)

        read = script([{"op": "list_parameters"}, {"op": "get_parameter", "id": identifier}])
        assert read["phase1_passed"] is True and len(read["results"]) == 2, read
        assert abs(read["results"][1]["value"] - original) < 1e-6, read
        assert read["files"] == [], read
        edited = script([
            {"op": "set_parameter", "id": identifier, "value": changed},
            {"op": "get_parameter", "id": identifier},
            {"op": "save_state", "name": "changed.vstpreset"},
        ], write=True)
        assert edited["phase1_passed"] is True, edited
        assert abs(edited["results"][0]["previous"] - original) < 1e-6, edited
        actual = edited["results"][1]["value"]
        assert abs(actual - changed) < 1e-5, edited
        assert edited["audit"], "Successful mutation needs a bounded audit trail."
        state = folder / "changed.vstpreset"
        assert state.is_file() and state.stat().st_size > 48, edited
        assert (state.name, "state") in {(item["name"], item["kind"]) for item in edited["files"]}, edited
        state_digest = hashlib.sha256(state.read_bytes()).digest()
        restored = script([
            {"op": "load_state", "name": state.name},
            {"op": "get_parameter", "id": identifier},
        ], write=True)
        assert abs(restored["results"][1]["value"] - actual) < 1e-5, restored
        # Standard preset loading must also work in the existing render path.
        invoke(worker, *arguments, "--preset", state, "--midi", midi,
               "--output", folder / "preset-render.wav")
        roundtrip = script([
            {"op": "save_state", "name": "baseline.vstpreset"},
            {"op": "set_parameter", "id": identifier, "value": changed},
            {"op": "load_state", "name": "baseline.vstpreset"},
            {"op": "get_parameter", "id": identifier},
            {"op": "render", "midi": midi.name, "output": "restored.wav"},
        ], write=True)
        assert abs(roundtrip["results"][3]["value"] - original) < 1e-5, roundtrip
        invoke(worker, *arguments, "--midi", midi, "--output", folder / "original.wav")
        assert (folder / "restored.wav").read_bytes() == (folder / "original.wav").read_bytes(), (
            "Loading state must clear parameter changes pending before the restore."
        )
        # Parameter changes must reach the processor, not merely its edit controller.
        rendered = script([
            {"op": "set_parameter", "id": identifier, "value": 0.2},
            {"op": "midi", "path": midi.name},
            {"op": "render", "output": "low.wav"},
            {"op": "set_parameter", "id": identifier, "value": 0.8},
            {"op": "render", "midi": midi.name, "output": "high.wav"},
        ], write=True)
        assert len(rendered["files"]) == 2, rendered
        for name in ("low.wav", "high.wav"):
            with wave.open(str(folder / name), "rb") as audio:
                assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()) == (2, 2, 48000, 307200)
                assert any(audio.readframes(48000)), name
        assert (folder / "low.wav").read_bytes() != (folder / "high.wav").read_bytes(), parameter

        # Read-only is the default, including for file and MIDI operations.
        for operation in (
            {"op": "set_parameter", "id": identifier, "value": changed},
            {"op": "midi", "path": midi.name},
            {"op": "render", "midi": midi.name, "output": "forbidden.wav"},
            {"op": "save_state", "name": "forbidden.vstpreset"},
            {"op": "load_state", "name": state.name},
        ):
            script([operation], expected_error="PERMISSION_DENIED")
        for value in (-0.01, 1.01, "0.5", True, None):
            script([{"op": "set_parameter", "id": identifier, "value": value}], write=True,
                   expected_error="INVALID_PARAMETER")
        for invalid_id in (0xFFFFFFFF, -1, 0x100000000, 0.9, True, "0"):
            script([{"op": "get_parameter", "id": invalid_id}], expected_error="INVALID_PARAMETER")
        for operation in (
            {"op": "exec", "command": "echo forbidden"},
            {"op": "get_parameter", "id": identifier, "code": "process.exit()"},
        ):
            script([operation], write=True, expected_error="INVALID_SCRIPT")
        script([{"op": "inspect"}], document_extra={"imports": ["os"]}, expected_error="INVALID_SCRIPT")
        for invalid_version in (1.5, True, "1", 0, 2):
            script([{"op": "inspect"}], document_extra={"version": invalid_version}, expected_error="INVALID_SCRIPT")
        for duplicate in ('{"version":1,"version":1,"operations":[{"op":"inspect"}]}',
                          '{"version":1,"operations":[{"op":"inspect","op":"inspect"}]}'):
            request.write_text(duplicate, encoding="utf-8")
            invoke(worker, "script", *arguments, "--script", request, "--workspace", folder,
                   expected_error="INVALID_SCRIPT")
        script([{"op": "inspect"}, {"op": "inspect"}], limits={"max_operations": 1}, expected_error="OPERATION_LIMIT")
        for key, invalid_values in (("max_operations", (257, 1.5, True, 2**64)),
                                    ("timeout_ms", (0, 120001, 1.5, True, 2**64))):
            for invalid_value in invalid_values:
                script([{"op": "inspect"}], limits={key: invalid_value}, expected_error="INVALID_SCRIPT")
        for path in ("../escape.vstpreset", str(folder.parent / "absolute.vstpreset"), "sub/tone.vstpreset",
                     "NUL.vstpreset", "CON.vstpreset"):
            script([{"op": "save_state", "name": path}], write=True, expected_error="INVALID_PATH")
        outside = folder.parent / "outside.mid"
        outside.write_bytes(midi.read_bytes())
        linked_input = folder / "linked.mid"
        linked_output = folder / "linked.wav"
        try:
            linked_input.symlink_to(outside)
            linked_output.symlink_to(outside)
        except OSError:
            # Windows runners can deny symlink creation without Developer Mode.
            pass
        else:
            script([{"op": "midi", "path": linked_input.name}], write=True, expected_error="INVALID_PATH")
            script([{"op": "render", "midi": midi.name, "output": linked_output.name}],
                   write=True, expected_error="INVALID_PATH")
            assert hashlib.sha256(outside.read_bytes()).digest() == midi_digest
        # Never overwrite an existing state, even with an explicit write grant.
        collision = script([{"op": "set_parameter", "id": identifier, "value": original},
                            {"op": "save_state", "name": state.name}], write=True,
                           expected_error="OUTPUT_EXISTS")
        assert hashlib.sha256(state.read_bytes()).digest() == state_digest, collision

        # A failure after an actual parameter mutation restores the in-process state
        # and must not publish outputs staged by earlier operations.
        malformed = folder / "invalid.mid"
        malformed.write_bytes(b"not a MIDI file")
        failed = script([
            {"op": "set_parameter", "id": identifier, "value": changed},
            {"op": "save_state", "name": "not-published.vstpreset"},
            {"op": "render", "midi": malformed.name, "output": "not-published.wav"},
        ], write=True, expected_error="INVALID_MIDI")
        assert failed["error"]["rollback"]["attempted"] is True, failed
        assert failed["error"]["rollback"]["restored"] is True, failed
        assert not (folder / "not-published.vstpreset").exists(), failed
        assert not (folder / "not-published.wav").exists(), failed
        assert not list(folder.glob("*.part")), list(folder.iterdir())
        started = monotonic()
        script([{"op": "render", "midi": midi.name, "output": "timed-out.wav"}],
               write=True, limits={"timeout_ms": 10}, extra=("--realtime",), expected_error="SCRIPT_TIMEOUT")
        assert monotonic() - started < 5, "Cooperative script deadline was not observed."
        assert not (folder / "timed-out.wav").exists()
        assert hashlib.sha256(midi.read_bytes()).digest() == midi_digest
        fresh = script([{"op": "get_parameter", "id": identifier}])
        assert abs(fresh["results"][0]["value"] - original) < 1e-6, fresh
    print(f"Phase 2 passed: {piano['name']}, parameter {identifier}, audible processor change, "
          "state round-trip, permission/schema/path/resource limits, rollback and no partial publication")


if __name__ == "__main__":
    main(*sys.argv[1:])
