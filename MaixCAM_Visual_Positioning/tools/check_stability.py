"""Observe live transport for several minutes without changing calibration/mode.

Requires a connected console and Pillow. Saves counts/timings, never room images
or device identities. Any observed disconnect or bad JPEG fails the check.
"""
import argparse
import base64
import hashlib
import io
import json
import sys
import time
import urllib.request
from datetime import datetime,timezone
from pathlib import Path

from PIL import Image


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--url',default='http://127.0.0.1:29860')
    p.add_argument('--duration',type=float,default=180)
    args=p.parse_args()
    if args.duration<10:p.error('duration must be at least 10 seconds')
    root=Path(__file__).resolve().parents[1]
    op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def status():
        with op.open(args.url+'/api/status',timeout=3) as r:return json.load(r)
    initial=status()
    assert initial.get('application')=='maixcam-ground-console'
    assert initial['device']['connected'],'请先连接真实相机并等到画面恢复'
    report={'tested_at_utc':datetime.now(timezone.utc).isoformat(),
        'test_type':'continuous live transport observation; no physical ground calibration',
        'application_version':initial['version'],'duration_requested_s':args.duration,
        'mode_observed':initial['device']['metadata']['mode'],
        'status_samples':0,'disconnected_samples':0,'jpeg_images_decoded':0,
        'max_status_latency_s':0.,'max_frame_age_s':0.,'errors':[],
        'field_accuracy_tested':False,'passed':False}
    start=time.monotonic();last_jpeg=-1.;next_progress=30
    while time.monotonic()-start<args.duration:
        tick=time.monotonic()
        try:
            s=status();d=s['device'];report['status_samples']+=1
            report['max_status_latency_s']=max(report['max_status_latency_s'],time.monotonic()-tick)
            report['max_frame_age_s']=max(report['max_frame_age_s'],d.get('frame_age_s') or 0.)
            if not d['connected']:report['disconnected_samples']+=1
            if d['connected'] and tick-last_jpeg>=1:
                with op.open(args.url+'/api/frame.jpg',timeout=3) as r:
                    jpeg=r.read();meta=json.loads(base64.b64decode(r.headers['X-Frame-Meta']))
                img=Image.open(io.BytesIO(jpeg));img.load()
                assert img.size==(640,480) and [meta['width'],meta['height']]==[640,480]
                report['jpeg_images_decoded']+=1;last_jpeg=tick
        except Exception as e:
            if len(report['errors'])<12:report['errors'].append({'elapsed_s':round(tick-start,2),'error':f'{type(e).__name__}: {e}'})
        elapsed=time.monotonic()-start
        if elapsed>=next_progress:
            print(f"{elapsed:.0f}s: status={report['status_samples']}, offline={report['disconnected_samples']}, JPEG={report['jpeg_images_decoded']}",flush=True)
            next_progress+=30
        time.sleep(max(0,.2-(time.monotonic()-tick)))
    final=status()
    report['duration_observed_s']=round(time.monotonic()-start,2)
    report['calibration_untouched']=initial['calibration']==final['calibration']
    report['mode_unchanged']=initial['device']['metadata']['mode']==final['device']['metadata']['mode']
    report['max_status_latency_s']=round(report['max_status_latency_s'],4)
    report['max_frame_age_s']=round(report['max_frame_age_s'],4)
    report['exe_sha256']=hashlib.sha256((root/'MaixVisionConsole.exe').read_bytes()).hexdigest()
    report['passed']=bool(not report['errors'] and not report['disconnected_samples'] and
        final['device']['connected'] and report['jpeg_images_decoded']>=args.duration*.7 and
        report['max_status_latency_s']<1.5 and report['calibration_untouched'] and report['mode_unchanged'])
    (root/'docs'/'stability-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
