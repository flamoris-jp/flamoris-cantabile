"""Real MDA state -> durable shelf -> isolated preview -> native project restart.

Only SDK test instruments, original notes and temporary media/state are used.
"""
import json
import os
from pathlib import Path
import sys
import tempfile

from band_mcp_smoke import wait, test_host


def main():
    worker, plugin, core = [str(Path(p).resolve()) for p in sys.argv[1:4]]
    os.environ.update(CANTABILE_RENDERER=worker, CANTABILE_CORE_LIBRARY=core,
                      CANTABILE_VST3_PATHS=plugin, CANTABILE_VST3_SCRIPT_WRITE='true')
    for name in ('CANTABILE_VST3_MODULE','CANTABILE_VST3_PRESET','CANTABILE_VST3_CLASS_ID'):
        os.environ.pop(name,None)
    test_host.CORE, test_host.RENDERER = Path(core),Path(worker)
    with tempfile.TemporaryDirectory() as directory:
        client=test_host.Client(directory)
        try:
            instruments=wait(client,client.tool('vst_scan', {}))['result']['instruments']
            piano=next(i for i in instruments if i['name']=='mda Piano' and i['usable'])
            client.tool('edit_batch', {'commands':[{'op':'track.add','track':{'id':f'p{i}','volume':0.5,'pan':i*0.5,
                'events':[{'kind':'note','tick':0,'duration':1440,'pitch':60,'velocity':100}]}} for i in range(2)]})
            client.tool('instrument_assign', {'track_id':'p0','catalog_id':piano['catalog_id']})
            sounds=[]
            for value in (0.1,0.9):
                result=wait(client,client.tool('vst_script_begin', {'track_id':'p0','script':{'version':1,'operations':[
                    {'op':'set_parameter','id':0,'value':value},{'op':'save_state','name':'changed.vstpreset'}]}}))['result']
                artifact=result['artifacts'][0]
                # Full source retrieval verifies the worker snapshot digest.
                client.download(artifact['file_id'],chunk=131072)
                sound=client.tool('sound_register', {'track_id':'p0','file_id':artifact['file_id'],
                    'display_name':f'Test piano {value}','category':'keys','roles':['chords'],
                    'classification_source':'SDK test instrument; original notes'})
                assert sound['state']['sha256']==artifact['sha256']
                assert sound['restoration']=='unverified'
                sounds.append(sound)
                client.tool('file_release', {'file_id':artifact['file_id']})
            for i,sound in enumerate(sounds):
                client.tool('sound_assign', {'sound_id':sound['sound_id'],'track_id':f'p{i}'})
            project=client.tool('project_get', {},guard=False)['project']
            assert project['tracks'][0]['instrument']['state_base64']!=project['tracks'][1]['instrument']['state_base64']
            client.tool('project_save', {})
            # Establish a baseline, then repeat with a new host/worker session.
            before=[]
            for sound in sounds:
                snapshot=client.tool('project_get', {},guard=False)
                result=wait(client,client.tool('sound_preview', {'sound_id':sound['sound_id'],'phrase':'melody'}))['result']
                before.append(client.download(result['render']['artifact']['file_id'],chunk=131072))
                assert client.tool('project_get', {},guard=False)==snapshot
            assert before[0]!=before[1], 'Changed settings must change actual audio'
            client.close(); client=test_host.Client(directory)
            assert client.tool('project_get', {},guard=False)['project']==project
            for i,(sound,value) in enumerate(zip(sounds,(0.1,0.9))):
                result=client.tool('vst_script', {'track_id':f'p{i}','script':{'version':1,'operations':[
                    {'op':'get_parameter','id':0}]}},guard=False)
                assert abs(result['results'][0]['value']-value)<1e-5,result
                after=wait(client,client.tool('sound_preview', {'sound_id':sound['sound_id'],'phrase':'melody'}))['result']
                assert client.download(after['render']['artifact']['file_id'],chunk=131072)==before[i]
            shelf=client.tool('sound_search', {'category':'keys','role':'chords'},guard=False)
            assert shelf['total']==2
            assert directory not in json.dumps(shelf) and 'state_base64' not in json.dumps(shelf).lower()
            for sound in sounds:
                client.tool('sound_remove', {'sound_id':sound['sound_id'],'expected_sha256':sound['state']['sha256']})
            assert client.tool('sound_search', {},guard=False)['total']==0
            rendered=wait(client,client.tool('audio_render', {'max_seconds':10}))['result']
            assert len(rendered['stems'])==2 and rendered['audio']['clipped_samples']==0
            client.download(rendered['artifact']['file_id'],chunk=131072)
            print('Sound shelf: real changed-state retention, distinct audio, restart, preview isolation and independent project copies passed.')
        finally: client.close()


if __name__=='__main__': main()
