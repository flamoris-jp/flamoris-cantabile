"""Registered-root bounds and privacy, using synthetic files, no NI contents."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_host import Client


class KontaktLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / "synthetic-library"
        self.library.mkdir()
        (self.library / "Instruments").mkdir()
        (self.library / "Instruments/Original.nki").write_bytes(b"not a real instrument")
        (self.library / "Variation.nksn").write_bytes(b"not a real snapshot")
        (self.library / "sample.wav").write_bytes(b"never enumerate or read samples")
        self.config = self.root / "libraries.json"
        self.config.write_text(json.dumps({"version": 1, "libraries": [
            {"id": "original", "name": "Synthetic library", "root": str(self.library)}]}))
        with patch.dict(os.environ, CANTABILE_KONTAKT_LIBRARIES=str(self.config), CANTABILE_KONTAKT_PREPARE="false"):
            self.client = Client(self.root / "workspace")

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def failure(self, data, code):
        response = self.client.tool_result("kontakt_library_list", data, guard=False)
        self.assertTrue(response.get("isError"), response)
        self.assertIn(code, json.dumps(response))

    def test_names_ids_paging_and_no_bytes_or_absolute_paths(self):
        roots = self.client.tool("kontakt_library_list", {}, guard=False)
        self.assertEqual(roots["libraries"], [{"library_id": "original", "display_name": "Synthetic library"}])
        first = self.client.tool("kontakt_library_list", {"library_id": "original", "limit": 1}, guard=False)
        second = self.client.tool("kontakt_library_list", {"library_id": "original", "limit": 1, "offset": 1}, guard=False)
        self.assertEqual(first["total"], 2)
        self.assertEqual(first["next_offset"], 1)
        self.assertIsNone(second["next_offset"])
        self.assertEqual(first["items"][0]["kind"], "instrument")
        self.assertEqual(second["items"][0]["kind"], "snapshot")
        self.assertNotIn(str(self.root), json.dumps([roots, first, second]))
        self.assertNotIn("not a real", json.dumps(first))
        again = self.client.tool("kontakt_library_list", {"library_id": "original"}, guard=False)
        self.assertEqual(again["items"][0], first["items"][0])

    def test_reparse_and_outside_content_are_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "Private.nki").write_bytes(b"outside")
        try:
            (self.library / "link").symlink_to(outside, target_is_directory=True)
            (self.library / "linked.nki").symlink_to(outside / "Private.nki")
        except OSError:
            self.skipTest("symlinks unavailable")
        result = self.client.tool("kontakt_library_list", {"library_id": "original"}, guard=False)
        self.assertEqual(result["total"], 2)
        self.assertNotIn("Private", json.dumps(result))

    def test_disabled_prepare_is_atomic_and_forbids_caller_code_and_paths(self):
        before = self.client.tool("project_get", {})
        request = {"catalog_id": "arbitrary", "library_id": "original", "instrument_id": "unknown"}
        response = self.client.tool_result("kontakt_prepare_begin", request)
        self.assertIn("kontakt_prepare_disabled", json.dumps(response))
        for field in ("script", "path", "instrument"):
            response = self.client.tool_result("kontakt_prepare_begin", dict(request, **{field: "not permitted"}))
            self.assertTrue(response.get("isError"), response)
        self.assertEqual(self.client.tool("project_get", {}), before)

    def test_readonly_has_no_prepare_and_rejects_it(self):
        self.client.close()
        with patch.dict(os.environ, CANTABILE_KONTAKT_LIBRARIES=str(self.config)):
            self.client = Client(self.root / "workspace", "readOnly")
        names = {x["name"] for x in self.client.rpc("tools/list", {})["result"]["tools"]}
        self.assertIn("kontakt_library_list", names)
        self.assertNotIn("kontakt_prepare_begin", names)
        self.assertTrue(self.client.tool_result("kontakt_prepare_begin", {
            "catalog_id": "x", "library_id": "original", "instrument_id": "y"}).get("isError"))

    def test_invalid_configuration_and_missing_root_fail_without_path(self):
        self.failure({"library_id": "unknown"}, "kontakt_library_unknown")
        self.failure({"library_id": "original", "root": "/"}, "invalid_request")
        self.config.write_text('{"version":1,"libraries":[],"shell":"forbidden"}')
        self.failure({}, "kontakt_configuration_invalid")
        self.config.write_text(json.dumps({"version": 1, "libraries": [
            {"id": "original", "name": "Synthetic", "root": str(self.root / "missing")}]}))
        self.failure({"library_id": "original"}, "kontakt_library_missing")

    def test_null_duplicate_config_and_catalog_limits_fail_closed(self):
        for libraries in ([{"id": None, "name": "Synthetic", "root": str(self.library)}],
                          [{"id": "x", "name": "Synthetic", "root": str(self.library)}] * 2):
            self.config.write_text(json.dumps({"version": 1, "libraries": libraries}))
            self.failure({}, "kontakt_configuration_invalid")
        self.config.write_text(json.dumps({"version": 1, "libraries": [
            {"id": "original", "name": "Synthetic", "root": str(self.library)}]}))
        for i in range(2048):
            (self.library / (str(i) + ".nki")).write_bytes(b"synthetic")
        self.failure({"library_id": "original"}, "kontakt_catalog_limit")


if __name__ == "__main__":
    unittest.main()
