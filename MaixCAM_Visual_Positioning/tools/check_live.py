"""Exercise frame transport/mode changes without writing ground calibration.

Requires a running, connected console and Pillow. Restores detection to off when
the device remains reachable. Saves partial failures as well as passing checks.
"""
import argparse
import base64
import io
import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


def main():
    modes=['off','yolo11','red_blob','apriltag']
    p=argparse.ArgumentParser()
    p.add_argument('--url',default='http://127.0.0.1:29860')
    p.add_argument('--modes',nargs='+',choices=modes,default=modes)
    args=p.parse_args()
    op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(path,body=None):
        req=urllib.request.Request(args.url+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with op.open(req,timeout=6) as r:return json.load(r)
    initial=api('/api/status')
    assert initial.get('application')=='maixcam-ground-console'
    assert initial['device']['connected'],'请先在上位机连接真实相机'
    report={'tested_at_utc':datetime.now(timezone.utc).isoformat(),
            'application_version':initial['version'],
            'exe_sha256':hashlib.sha256((Path(__file__).resolve().parents[1]/'MaixVisionConsole.exe').read_bytes()).hexdigest(),
            'test_type':'live camera transport and inference execution; no ground field calibration',
            'requested_modes':args.modes,'passed':False,'modes':{},
            'field_accuracy_tested':False,'apriltag_with_physical_tag_tested':False}
    failed=False
    try:
        for mode in args.modes:
            api('/api/mode',{'mode':mode})
            deadline=time.monotonic()+12
            while time.monotonic()<deadline:
                s=api('/api/status');meta=s['device']['metadata']
                if s['device']['connected'] and meta.get('mode')==mode:break
                time.sleep(.3)
            else:raise RuntimeError(f'{mode}: '+s['device']['message'])
            start_seq=meta['seq'];time.sleep(3.2)
            with op.open(args.url+'/api/frame.jpg',timeout=6) as r:
                jpeg=r.read();meta=json.loads(base64.b64decode(r.headers['X-Frame-Meta']))
            img=Image.open(io.BytesIO(jpeg));img.load();assert img.size==(640,480)
            assert meta['seq']>start_seq and meta['mode']==mode
            report['modes'][mode]={'image_size':list(img.size),'sequence_advanced':True,'fps_reported':meta['fps'],'detection_count':len(meta['detections']),'labels':sorted(set(o['label'] for o in meta['detections']))}
            print(mode,report['modes'][mode],flush=True)
        report['calibration_untouched']=initial['calibration']==api('/api/status')['calibration']
        assert report['calibration_untouched']
        report['passed']=True
    except Exception as e:
        failed=True
        report['failed_mode']=mode
        report['error']=f'{type(e).__name__}: {e}'
        print(report['error'],file=sys.stderr,flush=True)
    finally:
        try:
            api('/api/mode',{'mode':'off'})
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                s=api('/api/status')['device']
                if s['connected'] and s['metadata'].get('mode')=='off':
                    report['restored_mode']='off'
                    break
                time.sleep(.3)
            else:raise RuntimeError('未确认恢复到关闭识别模式')
        except Exception as e:
            failed=True;report['passed']=False
            report['cleanup_error']=f'{type(e).__name__}: {e}'
    out=Path(__file__).resolve().parents[1]/'docs'/'live-validation.json'
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 1 if failed else 0


if __name__=='__main__':sys.exit(main())
