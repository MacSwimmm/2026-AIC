"""YOLO switching, takeover during inference, immediate reconnect and long run."""
import base64
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
import urllib.request
from datetime import datetime,timezone

from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from device import ssh_connect,remote


def main():
    base='http://127.0.0.1:29860';op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(path,body=None):
        req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with op.open(req,timeout=10) as r:return json.load(r)
    initial=api('/api/status')
    report={'tested_at_utc':datetime.now(timezone.utc).isoformat(),'application_version':initial['version'],
        'exe_sha256':hashlib.sha256((ROOT/'MaixVisionConsole.exe').read_bytes()).hexdigest(),
        'test_type':'real YOLO mode switching, reconnect during inference and ten-minute YOLO observation',
        'cycles':[],'inference_reconnects':[],'immediate_reconnect':False,
        'field_accuracy_tested':False,'passed':False}
    def live(mode=None,timeout=60):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            s=api('/api/status');d=s['device'];meta=d['metadata']
            if d['connected'] and meta.get('seq') and (mode is None or meta.get('mode')==mode):return s
            if not d['connected'] and not d['busy'] and not d.get('reconnecting'):
                raise RuntimeError(d['message'])
            time.sleep(.15)
        raise RuntimeError(f'no advancing frame in mode {mode}: '+d['message'])
    def mode(name):
        start=time.monotonic();api('/api/mode',{'mode':name});s=live(name,timeout=25)
        seq=s['device']['metadata']['seq'];session=s['device']['metadata']['session_id']
        time.sleep(.4)
        with op.open(base+'/api/frame.jpg',timeout=5) as r:
            jpg=r.read();m=json.loads(base64.b64decode(r.headers['X-Frame-Meta']))
        image=Image.open(io.BytesIO(jpg));image.load()
        assert image.size==(640,480) and m['seq']>seq and m['session_id']==session and m['mode']==name
        return round(time.monotonic()-start,3)
    try:
        if not initial['device']['connected']:
            api('/api/connect',{});live('off')
        for index in range(10):
            on=mode('yolo11');off=mode('off')
            report['cycles'].append({'cycle':index+1,'yolo_seconds':on,'off_seconds':off,'advancing_jpeg_both_modes':True})
            print('YOLO switch cycle',index+1,'passed',flush=True)
        for index in range(3):
            mode('yolo11');start=time.monotonic();api('/api/connect',{});live('off')
            report['inference_reconnects'].append({'attempt':index+1,'seconds':round(time.monotonic()-start,3),'passed':True})
            print('Reconnect during YOLO',index+1,'passed',flush=True)
        mode('yolo11');api('/api/disconnect',{});api('/api/connect',{});live('off')
        report['immediate_reconnect']=True;print('Immediate disconnect/reconnect passed',flush=True)
        mode('yolo11')
        # A transient D state is expected during driver I/O; record progress
        # around sampling instead of misclassifying one state as a deadlock.
        s=api('/api/status');first=s['device']['metadata']['seq']
        c=ssh_connect(s['device']['host'],'root','root',ROOT/'data'/'known_hosts')
        try:
            code="""import time,collections,json
p=open('/root/aic_vision/agent.pid').read().strip();counts=collections.Counter()
for i in range(200):
 counts[open('/proc/'+p+'/stat').read().rsplit(') ',1)[1][0]]+=1;time.sleep(.005)
print(json.dumps(dict(counts)))
"""
            states=json.loads(remote(c,'python3 -u -c '+shlex.quote(code),timeout=5))
        finally:c.close()
        last=api('/api/status')['device']['metadata']['seq'];assert last>first
        report['live_yolo_process_states']={'samples':states,'frame_sequence_advanced_during_observation':True}
        print('YOLO state sampling',states,'frames advancing',flush=True)
        subprocess.run([sys.executable,'-u',str(ROOT/'tools/check_stability.py'),'--duration','600'],check=True,cwd=ROOT)
        stability=json.loads((ROOT/'docs/stability-validation.json').read_text('utf8'))
        assert stability['passed'] and stability['mode_observed']=='yolo11'
        report['continuous_yolo']=stability
        assert initial['calibration']==api('/api/status')['calibration']
        report['calibration_untouched']=True;report['passed']=True
    except Exception as e:
        import traceback;traceback.print_exc();report['error']=f'{type(e).__name__}: {e}'
    finally:
        try:
            mode('off');report['restored_mode']='off'
        except Exception as e:
            report['passed']=False;report['cleanup_error']=f'{type(e).__name__}: {e}'
        (ROOT/'docs/yolo-regression-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
