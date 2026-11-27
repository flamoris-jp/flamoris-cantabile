"""Sound retention, authority and failure contracts with an operator fixture.

Real standard VST state semantics are tested separately by sound_shelf_smoke.py.
No installed inventory, commercial state or media is checked in.
"""
import base64
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_host
from test_host import Client, CORE, DOTNET, HOST
from test_vst_safety import environment, script


WORKER = '''
import json, pathlib, struct, sys, time
args = sys.argv[1:]
identity = args[args.index('--class-id') + 1] if '--class-id' in args else '00000000000000000000000000000001'
if args[0] == 'list':
    print(json.dumps({'ok':True,'classes':[{'id':identity,'name':'Test keys','vendor':'Fixture','version':'1','kind':'instrument'}]}))
elif args[0] == 'inspect':
    print(json.dumps({'ok':True,'render_supported':True,'buses':[
      {'media_type':'event','direction':'input','index':0,'channel_count':16,'bus_type':0},
      {'media_type':'audio','direction':'output','index':0,'channel_count':2,'bus_type':0}]}))
elif args[0] == 'script':
    request = json.loads(pathlib.Path(args[args.index('--script')+1]).read_text())
    files = []
    value = 0.125
    for op in request['operations']:
        if op['op'] == 'set_parameter': value = op['value']
        if op['op'] == 'save_state':
            data = b'VST3'+struct.pack('<I',1)+identity.encode()+struct.pack('<q',52)+struct.pack('<f',value)
            data += b'List'+struct.pack('<I',1)+b'Comp'+struct.pack('<qq',48,4)
            pathlib.Path(op['name']).write_bytes(data)
            files.append({'name':op['name'],'kind':'state'})
    print(json.dumps({'ok':True,'phase1_passed':True,'results':[],'files':files}))
else:
    value = 0.125
    if '--preset' in args:
        data = pathlib.Path(args[args.index('--preset')+1]).read_bytes()
        value = struct.unpack_from('<f',data,48)[0]
    if value > 0.7: time.sleep(20)
    if value == 0.25 and ('--preroll-ms' not in args or int(args[args.index('--preroll-ms')+1]) < 250): value = 0
    data = struct.pack('<200f',*([value]*200))
    output = pathlib.Path(args[args.index('--output')+1])
    output.write_bytes(struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',36+len(data),b'WAVE',b'fmt ',16,3,2,48000,384000,8,32,b'data',len(data))+data)
    result = json.dumps({'ok':True,'backend':'vst3','sample_format':'ieee_float32le'})
    if '--result-json' in args:
        pathlib.Path(args[args.index('--result-json')+1]).write_text(result)
        print('private-vendor-stdout')
    else: print(result)
'''


@unittest.skipUnless(sys.platform.startswith('linux'), 'POSIX fixture executable')
class SoundShelfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HOST.exists() or not CORE.exists() or not Path(DOTNET).exists():
            raise unittest.SkipTest('Build native core and host first')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plugin = self.root / 'fixture.vst3'
        self.plugin.mkdir()
        self.worker = self.root / 'worker'
        self.worker.write_text(f'#!{sys.executable}\n' + WORKER)
        self.worker.chmod(0o700)
        self.workspace = self.root / 'workspace'
        self.client = None
        self.start()
        self.wait(self.client.tool('vst_scan', {}))
        self.catalog = self.client.tool('instruments_list', {}, guard=False)['catalog']['instruments'][0]['catalog_id']
        self.client.tool('edit_batch', {'commands': [
            {'op':'track.add','track':{'id':f'p{i}','volume':0.25+i/4,'pan':-0.3+i/2,'events':[
                {'kind':'note','tick':0,'duration':480,'pitch':60,'velocity':100}]}} for i in range(2)]})
        self.client.tool('instrument_assign', {'track_id':'p0','catalog_id':self.catalog})

    def start(self, permission='edit'):
        if self.client: self.client.close()
        with environment(CANTABILE_VST3_PATHS=str(self.plugin), CANTABILE_VST3_MODULE=None,
                         CANTABILE_VST3_PRESET=None, CANTABILE_VST3_CLASS_ID=None, CANTABILE_VST3_SCRIPT_WRITE='true'), \
                patch.object(test_host,'RENDERER',self.worker):
            self.client = Client(self.workspace, permission=permission)

    def tearDown(self):
        if self.client: self.client.close()
        self.temp.cleanup()

    def wait(self, job, expected='completed'):
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            result = self.client.tool('vst_job_status', {'job_id':job['job_id']}, guard=False)
            if result['state'] in {'completed','failed','cancelled'}:
                self.assertEqual(result['state'], expected, result)
                return result
            time.sleep(0.025)
        self.fail('job deadline')

    def register(self, value=0.125, preroll=0):
        result = self.client.tool('vst_script_write', {'track_id':'p0',**script(
            {'op':'set_parameter','id':0,'value':value},{'op':'save_state','name':'saved.vstpreset'})})
        artifact = result['artifacts'][0]
        sound = self.client.tool('sound_register', {'track_id':'p0','file_id':artifact['file_id'],
            'display_name':f'Fixture {value}','category':'keys','roles':['chords'],'tags':[],
            'classification_source':'fixture metadata; no timbre review','warmup_ms':4,'realtime':False,'preroll_ms':preroll})
        self.client.tool('file_release', {'file_id':artifact['file_id']})
        self.assertEqual(sound['state']['sha256'], artifact['sha256'])
        return sound

    def failure(self, name, value, code=None, **kwargs):
        response = self.client.tool_result(name, value, **kwargs)
        self.assertTrue(response.get('isError'), response)
        data = response.get('structuredContent') or json.loads(response['content'][0]['text'])
        if code: self.assertEqual(data['error']['code'], code, data)

    def project(self): return self.client.tool('project_get', {}, guard=False)

    def test_retained_states_independent_undo_restart_remove_and_preview(self):
        a,b = self.register(0.125),self.register(0.5)
        self.assertFalse(a['verification']['restored_audible'])
        self.assertFalse(a['verification']['human_reviewed'])
        found = self.client.tool('sound_search', {'category':'keys','role':'chords','limit':1}, guard=False)
        self.assertEqual(found['total'],2); self.assertEqual(found['next_offset'],1)
        self.assertNotIn(str(self.root),json.dumps(found)); self.assertNotIn('StateBase64',json.dumps(found))
        self.client.tool('sound_assign', {'track_id':'p0','sound_id':a['sound_id']})
        before = self.project()['project']
        self.client.tool('sound_assign', {'track_id':'p1','sound_id':b['sound_id']})
        assigned = self.project()['project']
        self.assertNotEqual(assigned['tracks'][0]['instrument']['state_base64'],assigned['tracks'][1]['instrument']['state_base64'])
        self.assertEqual(assigned['tracks'][1]['volume'],before['tracks'][1]['volume'])
        self.client.tool('history_undo', {}); self.assertEqual(self.project()['project'],before)
        self.client.tool('history_redo', {}); self.assertEqual(self.project()['project'],assigned)
        self.client.tool('project_save', {})
        self.start(); self.assertEqual(self.project()['project'],assigned)
        before = self.project()
        result = self.wait(self.client.tool('sound_preview', {'sound_id':a['sound_id'],'phrase':'melody'}))['result']
        self.assertEqual(self.project(),before)
        wave = self.client.download(result['render']['artifact']['file_id'],chunk=131072)
        self.assertGreater(len(wave),44)
        self.client.tool('sound_remove', {'sound_id':a['sound_id'],'expected_sha256':a['state']['sha256']})
        self.assertEqual(self.project(),before)
        self.assertEqual(self.wait(self.client.tool('audio_render', {}))['state'],'completed')
        self.start(); self.assertEqual(self.project()['project'],assigned)
        self.assertEqual(self.client.tool('sound_search', {},guard=False)['total'],1)

    def test_kontakt_render_stderr_budget_and_private_overflow_diagnostics(self):
        # Original operator-configured subprocess only; no vendor plugin/data.
        # Exercise the real catalog -> shelf preview / project-render route.
        cases = [
            ('Native Instruments', 'Kontakt 8', 32768, True, 1048576),
            ('Native Instruments', 'Kontakt 7', 32768, True, 1048576),
            ('Fixture', 'Kontakt 8', 32768, False, 16384),
            ('Native Instruments', 'Different instrument', 32768, False, 16384),
            ('Native Instruments', 'Kontakt 8', 1048577, False, 1048576),
        ]
        for vendor, name, count, succeeds, maximum in cases:
            with self.subTest(vendor=vendor, name=name, count=count):
                source = WORKER.replace("'name':'Test keys','vendor':'Fixture'",
                                        f"'name':{name!r},'vendor':{vendor!r}")
                source = source.replace("else:\n    value = 0.125", "else:\n"
                    f"    sys.stderr.write('private-diagnostic-' + 'x' * {count - 19})\n"
                    "    sys.stderr.flush()\n    value = 0.125")
                self.worker.write_text(f'#!{sys.executable}\n' + source)
                self.wait(self.client.tool('vst_scan', {}))
                sound = self.register()
                before = self.project()
                files = self.client.tool('file_list', {}, guard=False)
                preview = self.wait(self.client.tool('sound_preview', {'sound_id':sound['sound_id'], 'phrase':'melody'}),
                                    expected='completed' if succeeds else 'failed')
                self.assertEqual(self.project(), before)
                self.assertNotIn('private-diagnostic-', json.dumps(preview))
                self.assertNotIn('private-vendor-stdout', json.dumps(preview))
                self.assertNotIn(str(self.root), json.dumps(preview))
                if succeeds:
                    self.assertGreater(preview['result']['render']['audio']['peak'], 0)
                    self.client.tool('sound_assign', {'track_id':'p0', 'sound_id':sound['sound_id']})
                    self.client.tool('sound_assign', {'track_id':'p1', 'sound_id':sound['sound_id']})
                    project_render = self.wait(self.client.tool('audio_render', {}))
                    self.assertGreater(project_render['result']['audio']['peak'], 0)
                else:
                    error = preview['error']
                    self.assertEqual(error['code'], 'vst_output_limit')
                    self.assertEqual(error['track_id'], 'preview')
                    self.assertEqual(error['output_limit']['stream'], 'stderr')
                    self.assertEqual(error['output_limit']['maximum_characters'], maximum)
                    self.assertGreater(error['output_limit']['observed_characters'], maximum)
                    self.assertIn('host_kill_requested', error['termination'])
                    self.assertEqual(self.client.tool('file_list', {}, guard=False), files)

    def test_kontakt_result_file_is_required_and_validated_before_publication(self):
        source = WORKER.replace("'name':'Test keys','vendor':'Fixture'",
                                "'name':'Kontakt 8','vendor':'Native Instruments'")
        self.worker.write_text(f'#!{sys.executable}\n' + source)
        self.wait(self.client.tool('vst_scan', {}))
        sound = self.register()
        before = self.project()
        files = self.client.tool('file_list', {}, guard=False)
        original = "pathlib.Path(args[args.index('--result-json')+1]).write_text(result)"
        for operation, kind in [
            ("pass", "unavailable_result"),
            ("pathlib.Path(args[args.index('--result-json')+1]).write_text('private-invalid')", "invalid_json"),
            ("pathlib.Path(args[args.index('--result-json')+1]).write_text('x' * 16385)", "unavailable_result"),
            ("pathlib.Path(args[args.index('--result-json')+1]).symlink_to('0.mid')", "unavailable_result"),
        ]:
            with self.subTest(kind=kind, operation=operation):
                self.worker.write_text(f'#!{sys.executable}\n' + source.replace(original, operation))
                status = self.wait(self.client.tool('sound_preview', {'sound_id':sound['sound_id'], 'phrase':'melody'}), 'failed')
                error = status['error']
                self.assertEqual(error['code'], 'vst_worker_crashed')
                self.assertEqual(error['result_channel']['source'], 'file')
                self.assertEqual(error['result_channel']['kind'], kind)
                self.assertEqual(error['result_channel']['exit_code'], 0)
                self.assertNotIn('private-', json.dumps(status))
                self.assertNotIn(str(self.root), json.dumps(status))
                self.assertEqual(self.client.tool('file_list', {}, guard=False), files)
                self.assertEqual(self.project(), before)

    def test_validation_missing_version_corruption_and_guard_are_atomic(self):
        a = self.register(); before=self.project()
        self.failure('sound_assign', {'track_id':'absent','sound_id':a['sound_id']},'NOT_FOUND')
        self.failure('sound_assign', {'track_id':'p1','sound_id':a['sound_id']},stale=0)
        self.failure('sound_register', {'track_id':'p0','display_name':'No state','category':'keys','classification_source':'fixture'},'sound_state_required')
        self.failure('sound_remove', {'sound_id':a['sound_id'],'expected_sha256':'0'*64},'sound_changed')
        self.failure('sound_search', {'limit':True},'invalid_request',guard=False)
        self.failure('sound_preview', {'sound_id':a['sound_id'],'phrase':'vendor-midi'},'invalid_request')
        path=self.workspace/'sounds'/f"{a['sound_id']}.json"
        record=json.loads(path.read_text()); record['PluginVersion']='different'; path.write_text(json.dumps(record))
        self.failure('sound_assign', {'track_id':'p1','sound_id':a['sound_id']},'sound_version_mismatch')
        self.assertFalse(self.client.tool('sound_search', {},guard=False)['sounds'][0]['available'])
        record['PluginVersion']='1'; data=bytearray(base64.b64decode(record['StateBase64'])); data[48]^=1
        record['StateBase64']=base64.b64encode(data).decode(); path.write_text(json.dumps(record))
        self.failure('sound_assign', {'track_id':'p1','sound_id':a['sound_id']},'sound_state_corrupt')
        self.assertFalse(self.client.tool('sound_search', {},guard=False)['sounds'][0]['available'])
        self.assertEqual(self.project(),before)
        self.client.tool('sound_remove', {'sound_id':a['sound_id'],'expected_sha256':a['state']['sha256']})
        self.assertEqual(self.client.tool('sound_search', {},guard=False)['total'],0)

    def test_render_invalid_result_reports_exit_and_counts_without_text(self):
        sound = self.register()
        before = self.project()
        files = self.client.tool('file_list', {}, guard=False)
        for exit_code in (0, 17):
            with self.subTest(exit_code=exit_code):
                source = WORKER.replace("else:\n    value = 0.125", "else:\n"
                    "    sys.stderr.write('private-diagnostic')\n"
                    "    print('private-result')\n"
                    f"    sys.exit({exit_code})\n    value = 0.125")
                self.worker.write_text(f'#!{sys.executable}\n' + source)
                failure = self.wait(self.client.tool('sound_preview', {'sound_id':sound['sound_id'], 'phrase':'melody'}), expected='failed')
                error = failure['error']
                self.assertEqual(error['code'], 'vst_worker_crashed')
                self.assertEqual(error['track_id'], 'preview')
                channel = error['result_channel']
                self.assertEqual(channel['kind'], 'invalid_json')
                self.assertEqual(channel['exit_code'], exit_code)
                self.assertFalse(channel['stdout_empty'])
                self.assertFalse(channel['stdout_starts_with_object'])
                self.assertEqual(channel['stdout_characters'], len('private-result\n'))
                self.assertEqual(channel['stderr_characters'], len('private-diagnostic'))
                self.assertNotIn('private-result', json.dumps(failure))
                self.assertNotIn('private-diagnostic', json.dumps(failure))
                self.assertNotIn(str(self.root), json.dumps(failure))
                self.assertEqual(self.project(), before)
                self.assertEqual(self.client.tool('file_list', {}, guard=False), files)

    def test_missing_plugin_and_malformed_or_mismatched_state(self):
        a=self.register(); before=self.project()
        result=self.client.tool('vst_script_write', {'track_id':'p0',**script({'op':'save_state','name':'test.vstpreset'})})
        artifact=result['artifacts'][0]
        path=self.workspace/'exports'/f"{artifact['file_id']}.vstpreset"
        original=path.read_bytes()
        # Trusted-workspace fixture: publish a distinct validated artifact for
        # each malformed payload, avoiding the export change detection gate.
        import hashlib
        for data in (original[:48],original[:8]+b'F'*32+original[40:]):
            path.write_bytes(data)
            metadata=self.workspace/'exports'/f"{artifact['file_id']}.artifact.json"
            info=json.loads(metadata.read_text()); info['size']=len(data); info['sha256']=hashlib.sha256(data).hexdigest(); metadata.write_text(json.dumps(info))
            self.client.tool('project_save', {})
            self.start()
            self.failure('sound_register', {'track_id':'p0','file_id':artifact['file_id'],'display_name':'bad','category':'keys','classification_source':'fixture'},'invalid_instrument_state')
        self.plugin.rmdir()
        self.failure('sound_assign', {'sound_id':a['sound_id'],'track_id':'p1'},'instrument_missing')
        self.assertFalse(self.client.tool('sound_search', {},guard=False)['sounds'][0]['available'])
        self.assertEqual(self.project()['project'],before['project'])

    def test_sound_count_quota_does_not_publish_an_extra_entry(self):
        a=self.register()
        directory=self.workspace/'sounds'; original=json.loads((directory/f"{a['sound_id']}.json").read_text())
        for n in range(1,128):
            record=dict(original,SoundId=f'sound-{n:032x}')
            (directory/f"{record['SoundId']}.json").write_text(json.dumps(record))
        self.client.tool('sound_assign', {'track_id':'p0','sound_id':a['sound_id']})
        self.failure('sound_register', {'track_id':'p0','display_name':'extra','category':'keys','classification_source':'fixture'},'sound_limit')
        self.assertEqual(self.client.tool('sound_search', {},guard=False)['total'],128)

    def test_cancel_preview_and_delete_snapshot_do_not_edit_project(self):
        a=self.register(0.8); before=self.project()
        files=self.client.tool('file_list', {},guard=False)
        job=self.client.tool('sound_preview', {'sound_id':a['sound_id'],'phrase':'bass'})
        self.client.tool('sound_remove', {'sound_id':a['sound_id'],'expected_sha256':a['state']['sha256']})
        self.client.tool('audio_cancel', {'job_id':job['job_id']})
        self.wait(job,'cancelled')
        self.assertEqual(self.client.tool('file_list', {},guard=False),files)
        self.assertEqual(self.project(),before)

    def test_processed_preroll_reaches_preview_project_render_and_survives_restart(self):
        import struct
        a=self.register(0.25,preroll=250)
        self.assertEqual(a['loading']['preroll_ms'],250)
        before=self.project()
        result=self.wait(self.client.tool('sound_preview', {'sound_id':a['sound_id'],'phrase':'bass'}))['result']
        data=self.client.download(result['render']['artifact']['file_id'],chunk=131072)
        self.assertGreater(struct.unpack_from('<h',data,44)[0],0)
        self.assertEqual(self.project(),before)
        self.client.tool('sound_assign', {'sound_id':a['sound_id'],'track_id':'p0'})
        self.client.tool('edit_batch', {'commands':[{'op':'track.update','track_id':'p1','patch':{'muted':True}}]})
        self.assertEqual(self.project()['project']['tracks'][0]['instrument']['preroll_ms'],250)
        self.client.tool('project_save', {}); self.start()
        data=self.client.download(self.wait(self.client.tool('audio_render', {}))['result']['artifact']['file_id'],chunk=131072)
        self.assertGreater(struct.unpack_from('<h',data,44)[0],0)
        self.failure('sound_register', {'track_id':'p0','display_name':'bad','category':'keys','classification_source':'fixture','preroll_ms':30001},'invalid_request')
        # Pre-preroll records remain compatible and default to zero.
        path=self.workspace/'sounds'/f"{a['sound_id']}.json";entry=json.loads(path.read_text());del entry['PrerollMs'];path.write_text(json.dumps(entry))
        self.assertEqual(self.client.tool('sound_search', {},guard=False)['sounds'][0]['loading']['preroll_ms'],0)

    def test_attestations_are_distinct_and_readonly_cannot_mutate(self):
        a=self.register()
        verified=self.client.tool('sound_verify', {'sound_id':a['sound_id'],'initial_audible':True,
            'restored_audible':False,'human_reviewed':False,'evidence':'Fixture baseline measured only'})
        self.assertTrue(verified['verification']['initial_audible']); self.assertFalse(verified['verification']['restored_audible'])
        self.assertEqual(self.client.tool('sound_search', {'verification':'restored_audible'},guard=False)['total'],0)
        self.start('readOnly')
        names={t['name'] for t in self.client.rpc('tools/list', {})['result']['tools']}
        self.assertIn('sound_search',names)
        for name in ['sound_register','sound_assign','sound_preview','sound_verify','sound_remove']: self.assertNotIn(name,names)
        self.failure('sound_remove', {'sound_id':a['sound_id'],'expected_sha256':a['state']['sha256']})
        self.assertEqual(self.client.tool('sound_search', {},guard=False)['total'],1)


if __name__=='__main__': unittest.main()
