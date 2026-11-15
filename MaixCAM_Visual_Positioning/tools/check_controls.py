"""Verify native controls without camera pixels while the packaged PC app streams."""
import hashlib
import io
import json
import shlex
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from device import ssh_connect,remote


def main():
    op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def pc(path,body=None):
        with op.open(urllib.request.Request('http://127.0.0.1:29860'+path,
                data=None if body is None else json.dumps(body).encode(),
                headers={'Content-Type':'application/json'}),timeout=8) as r:return json.load(r)
    def wait(predicate):
        end=time.monotonic()+30
        while time.monotonic()<end:
            s=pc('/api/status')
            if predicate(s):return s
            time.sleep(.1)
        raise RuntimeError('PC connection did not reach expected state')
    if not pc('/api/status')['device']['connected']:
        pc('/api/connect',{});wait(lambda s:s['device']['connected'])
    initial=pc('/api/status')
    c=ssh_connect(initial['device']['host'],'root','root',ROOT/'data/known_hosts')
    try:cfg=json.loads(remote(c,'cat /root/aic_vision/agent_config.json'))
    except Exception:c.close();raise
    def request(path):
        return op.open(urllib.request.Request('http://'+initial['device']['host']+':18765'+path,
            headers={'Authorization':'Bearer '+cfg['token']}),timeout=4)
    def native():
        with request('/status') as r:return json.load(r)
    def wait_native(predicate):
        end=time.monotonic()+8
        while time.monotonic()<end:
            s=native()
            if predicate(s):return s
            time.sleep(.1)
        raise RuntimeError('Native controls/preview transition failed')
    def tap(x,y):
        code='''import os,struct,time
fd=os.open('/dev/input/event1',os.O_WRONLY)
def emit(t,c,v):os.write(fd,struct.pack('llHHi',0,0,t,c,v))
for t,c,v in [(3,53,(@Y@+1)%480),(3,54,(638-@X@)%640),(0,0,0),(3,47,0),(3,57,1),(3,53,@Y@),(3,54,639-@X@),(1,330,1),(0,0,0)]:emit(t,c,v)
time.sleep(.25)
for t,c,v in [(3,57,-1),(1,330,0),(0,0,0)]:emit(t,c,v)
os.close(fd)
'''.replace('@X@',str(x)).replace('@Y@',str(y))
        remote(c,'/usr/bin/python3 -c '+shlex.quote(code),timeout=4)
    report={'tested_at_utc':datetime.now(timezone.utc).isoformat(),'version':initial['version'],
        'exe_sha256':hashlib.sha256((ROOT/'MaixVisionConsole.exe').read_bytes()).hexdigest(),
        'native_package_sha256':hashlib.sha256((ROOT/'MaixGround-2.1.1.zip').read_bytes()).hexdigest(),
        'wireless_tested':False,'physical_accuracy_tested':False,'passed':False}
    try:
        first=wait_native(lambda s:s['pc_viewers']>0 and not s['local_preview_enabled'])
        time.sleep(.3);first=native();time.sleep(2);last=native()
        assert last['seq']>first['seq']
        assert last['local_preview_seq']==first['local_preview_seq']
        assert last['display_seq']==first['display_seq']
        report['camera_advances_without_local_preview_or_idle_screen_redraw']=True
        with request('/screen.jpg') as r:raw=r.read()
        image=Image.open(io.BytesIO(raw));image.load();assert image.size==(640,480)
        (ROOT/'data/qa/controls-only.jpg').write_bytes(raw)
        tap(565,185);wait_native(lambda s:s['screen_page']=='modes')
        tap(320,400);wait_native(lambda s:s['screen_page']=='live')
        assert native()['local_preview_seq']==first['local_preview_seq']
        report['native_touch_mode_menu_works_without_preview']=True
        pc('/api/disconnect',{})
        resumed=wait_native(lambda s:s['pc_viewers']==0 and s['local_preview_enabled'])
        wait_native(lambda s:s['local_preview_seq']>resumed['local_preview_seq'])
        report['pc_detach_automatically_restores_native_preview']=True
        pc('/api/connect',{});wait(lambda s:s['device']['connected'])
        wait_native(lambda s:s['pc_viewers']>0 and not s['local_preview_enabled'])
        final=pc('/api/status')
        assert initial['calibration']==final['calibration'] and initial['targets']==final['targets']
        assert initial['device']['metadata']['mode']==final['device']['metadata']['mode']
        report['reattach_hides_preview_again_and_preserves_mode_and_data']=True
        report['passed']=True
    finally:
        c.close()
        (ROOT/'docs/controls-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
