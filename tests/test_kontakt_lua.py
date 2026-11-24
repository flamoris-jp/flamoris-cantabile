"""Execute the actual generated Lua in Lua 5.4 with a synthetic Kontakt table.

Checks quoting/injection, selected arguments, failure receipts and completion
ordering. This tests Lua semantics, not the commercial Kontakt API implementation.
"""
import ctypes
import ctypes.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class KontaktLuaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = next((p for p in (ROOT / "build/cantabile-kontakt-lua-fixture", ROOT / "build/Release/cantabile-kontakt-lua-fixture.exe") if p.exists()), None)
        library = ctypes.util.find_library("lua5.4")
        if not cls.helper or not library:
            raise unittest.SkipTest("Build the Lua fixture and install the Lua 5.4 runtime for this independent gate")
        cls.lua = ctypes.CDLL(library)
        cls.lua.luaL_newstate.restype = ctypes.c_void_p
        cls.lua.luaL_openlibs.argtypes = [ctypes.c_void_p]
        cls.lua.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cls.lua.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ssize_t, ctypes.c_void_p]
        cls.lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cls.lua.lua_tolstring.restype = ctypes.c_char_p
        cls.lua.lua_close.argtypes = [ctypes.c_void_p]

    def execute(self, code):
        state = self.lua.luaL_newstate()
        self.assertTrue(state)
        try:
            self.lua.luaL_openlibs(state)
            result = self.lua.luaL_loadstring(state, code.encode()) or self.lua.lua_pcallk(state, 0, 0, 0, 0, None)
            if result:
                self.fail(self.lua.lua_tolstring(state, -1, None).decode())
        finally:
            self.lua.lua_close(state)

    def run_script(self, snapshot, fail=False):
        with tempfile.TemporaryDirectory() as temp:
            receipt = Path(temp) / "receipt.txt"
            # Deliberately dangerous syntax, Unicode and Windows path separators.
            instrument = 'D:\\日本語\\x\"); error("injected") --.nki'
            snap = 'D:\\音色\\a\"b\'c.nksn' if snapshot else None
            nonce = "c" * 32
            request = dict(version=1, instrument=instrument, snapshot=snap, nonce=nonce)
            generated = subprocess.run([str(self.helper)], input=json.dumps(dict(request=request, receipt=str(receipt))),
                                       text=True, capture_output=True, check=True, timeout=10).stdout
            # Hex literals keep the independent test prelude separate from the
            # production decimal-escape implementation.
            def string(value):
                return '"' + ''.join('\\x%02x' % x for x in value.encode()) + '"'
            prelude = '''
                local removed, loaded, count = 0, false, 2
                Kontakt = {}
                function Kontakt.get_instrument_indices() return {0,128} end
                function Kontakt.remove_instrument(i) assert(i==0 or i==128); removed=removed+1 end
                function Kontakt.load_instrument(path)
                    assert(removed==2 and path==EXPECTED_INSTRUMENT)
                    if FAIL_LOAD then error("synthetic load failure") end
                    loaded=true; count=1; return 256
                end
                function Kontakt.load_snapshot(index,path) assert(loaded and index==256 and path==EXPECTED_SNAPSHOT) end
                function Kontakt.set_instrument_midi_channel(index,ch) assert(loaded and index==256 and ch==1) end
                function Kontakt.get_num_instruments() return count end
            '''.replace("EXPECTED_INSTRUMENT", string(instrument)).replace("EXPECTED_SNAPSHOT", string(snap or "")).replace("FAIL_LOAD", "true" if fail else "false")
            self.execute(prelude + generated)
            self.assertEqual(receipt.read_text(), nonce + (":failed" if fail else ":ok"))
            self.assertFalse(Path(str(receipt) + ".part").exists())

    def test_selected_unicode_and_injection_are_literal_data(self):
        self.run_script(snapshot=True)

    def test_instrument_without_snapshot_and_failed_load_receipt(self):
        self.run_script(snapshot=False)
        self.run_script(snapshot=False, fail=True)
