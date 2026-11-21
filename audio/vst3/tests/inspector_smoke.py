"""Phase 1 gate: inspect a real SDK VST3 without changing its default state.

No commercial module or preset is distributed by this test. Run this gate before
script_smoke.py on every supported host platform.
"""
import json
import subprocess
import sys


def invoke(worker, *arguments, expected_error=None):
    result = subprocess.run(
        [worker, *map(str, arguments)], capture_output=True, text=True,
        encoding="utf-8", timeout=45,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError((result.returncode, result.stdout, result.stderr)) from error
    if expected_error is not None:
        assert result.returncode != 0, payload
        assert payload["ok"] is False, payload
        assert payload["error"]["code"] == expected_error, payload
    else:
        assert result.returncode == 0 and payload["ok"] is True, (payload, result.stderr)
    return payload


def piano_class(worker, plugin):
    classes = invoke(worker, "list", "--plugin", plugin)["instruments"]
    return next(item for item in classes if item["name"].lower() == "mda piano")


def inspect_piano(worker, plugin):
    piano = piano_class(worker, plugin)
    report = invoke(worker, "inspect", "--plugin", plugin, "--class-id", piano["id"])
    assert report["schema_version"] == 1, report
    identity = report["plugin"]
    assert identity["class_id"] == piano["id"] and identity["name"].lower() == "mda piano", identity
    for key in ("vendor", "version", "sdk_version"):
        assert isinstance(identity[key], str), identity
    parameters = report["parameters"]
    assert parameters, "The SDK piano publishes parameters."
    ids = set()
    for parameter in parameters:
        assert isinstance(parameter["id"], int) and 0 <= parameter["id"] <= 0xFFFFFFFF, parameter
        assert parameter["id"] not in ids, parameter
        ids.add(parameter["id"])
        for key in ("title", "short_title", "units", "display"):
            assert isinstance(parameter[key], str), parameter
        assert isinstance(parameter["flags"], int), parameter
        assert isinstance(parameter["step_count"], int) and parameter["step_count"] >= 0, parameter
        assert 0 <= parameter["value"] <= 1, parameter
        assert 0 <= parameter["default_value"] <= 1, parameter
    assert isinstance(report["buses"], list) and report["buses"], report
    assert {(bus["media_type"], bus["direction"]) for bus in report["buses"]} >= {
        ("audio", "output"), ("event", "input")
    }, report["buses"]
    for bus in report["buses"]:
        assert isinstance(bus["index"], int) and bus["index"] >= 0, bus
        assert isinstance(bus["name"], str) and bus["channel_count"] > 0, bus
        assert isinstance(bus["bus_type"], int) and isinstance(bus["flags"], int), bus
    assert isinstance(report["programs"], list), report
    assert sum(program["program_count"] for program in report["programs"]) == 8, report["programs"]
    for program_list in report["programs"]:
        assert len(program_list["programs"]) == program_list["program_count"], program_list
        assert all(isinstance(program["name"], str) for program in program_list["programs"]), program_list
    interfaces = report["interfaces"]
    assert isinstance(interfaces, dict) and interfaces, report
    assert all(isinstance(value, bool) for value in interfaces.values()), interfaces
    assert all(interfaces[name] for name in ("IComponent", "IEditController", "IAudioProcessor", "IMidiMapping", "IUnitInfo")), interfaces
    mapping = report["midi_mapping"]
    assert isinstance(mapping["supported"], bool) and isinstance(mapping["assignments"], list), mapping
    if not mapping["supported"]:
        assert mapping["assignments"] == [], mapping
    else:
        assert {(entry["channel"], entry["controller"]) for entry in mapping["assignments"]} >= {
            (channel, controller) for channel in range(16) for controller in (1, 64)
        }, mapping
        assert all(entry["id"] in ids and entry["bus"] == 0 and 0 <= entry["channel"] < 16
                   and 0 <= entry["controller"] <= 129 for entry in mapping["assignments"]), mapping
    midi_output = report["midi_output"]
    assert isinstance(midi_output["bus_count"], int) and midi_output["bus_count"] >= 0, midi_output
    assert midi_output["available"] == (midi_output["bus_count"] > 0), midi_output
    assert midi_output["capture_supported"] is False, midi_output
    # Repeat in a fresh process. Inspector must not alter any public parameter.
    repeat = invoke(worker, "inspect", "--plugin", plugin, "--class-id", piano["id"])
    assert [(p["id"], p["value"]) for p in repeat["parameters"]] == [
        (p["id"], p["value"]) for p in parameters
    ], "Read-only inspection changed the instrument defaults."
    return piano, report


def main(worker, plugin):
    piano, report = inspect_piano(worker, plugin)
    print(f"Phase 1 passed: {piano['name']}, {len(report['parameters'])} parameters, "
          f"{len(report['buses'])} buses, {len(report['midi_mapping']['assignments'])} MIDI mappings; "
          "capabilities and MIDI-output capture reported explicitly")


if __name__ == "__main__":
    main(*sys.argv[1:])
