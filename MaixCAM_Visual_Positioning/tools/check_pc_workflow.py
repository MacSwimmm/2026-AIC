"""Real device + packaged PC app: browser calibration, selection and saved targets.

Only runs on a device with no calibration/targets. Coordinates are synthetic;
this does not measure physical field accuracy or wireless throughput.
"""
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:29860'
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path,body=None):
    with op.open(urllib.request.Request(BASE+path,data=None if body is None else json.dumps(body).encode(),
        headers={'Content-Type':'application/json'}),timeout=8) as r:return json.load(r)


def wait(predicate,timeout=30):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        s=api('/api/status')
        if predicate(s):return s
        time.sleep(.15)
    raise RuntimeError('PC workflow timed out: '+str(s['device']['message']))


def main():
    report={'tested_at_utc':datetime.now(timezone.utc).isoformat(),'version':'2.1.0',
        'test':'packaged EXE, real USB MaixCAM and real browser; synthetic calibration',
        'physical_accuracy_tested':False,'wireless_tested':False,'passed':False,'errors':[]}
    own_cal=None
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1080})
            page.on('pageerror',lambda e:report['errors'].append(str(e)))
            page.add_init_script('''window.__frameIds=new Set();window.__decodedImages=new WeakSet();window.__decodedCount=0;
const fetch0=window.fetch;window.fetch=async(...a)=>{const r=await fetch0(...a);if(String(a[0]).includes('/api/frame.jpg')&&r.ok){const m=JSON.parse(atob(r.headers.get('X-Frame-Meta')));window.__frameIds.add(m.session_id+':'+m.seq);}return r;};
const draw0=CanvasRenderingContext2D.prototype.drawImage;CanvasRenderingContext2D.prototype.drawImage=function(...a){if(this.canvas.id==='cameraCanvas'&&a[0] instanceof HTMLImageElement&&!window.__decodedImages.has(a[0])){window.__decodedImages.add(a[0]);window.__decodedCount++;}return draw0.apply(this,a);};''')
            page.goto(BASE,wait_until='domcontentloaded');page.click('#connect')
            initial=wait(lambda s:s['device']['connected'] and bool(s['device']['metadata']))
            assert initial['calibration'] is None and not initial['targets'],'Preserve existing user data'
            page.wait_for_function('document.querySelector("#cameraEmpty").hidden')
            page.fill('#widthM','5');page.fill('#heightM','5');page.click('#startCalibrate')
            canvas=page.locator('#cameraCanvas');box=canvas.bounding_box()
            def point(x,y):canvas.click(position={'x':x*box['width']/640,'y':y*box['height']/480})
            for x,y in [(60,420),(580,420),(400,90),(240,90)]:point(x,y)
            page.click('#applyCalibration')
            calibrated=wait(lambda s:s['calibration_compatible'])
            own_cal=calibrated['calibration']['created_at']
            report['four_point_browser_calibration_saved_on_device']=True
            page.wait_for_timeout(700);point(320,167.64705882352942)
            page.wait_for_function('!document.querySelector("#saveTarget").disabled')
            page.click('#saveTarget');saved=wait(lambda s:len(s['targets'])==1)
            target=saved['targets'][0]
            assert abs(target['x']-2.5)<.01 and abs(target['y']-2.5)<.01
            exported=api('/api/targets/export');assert exported['targets']==saved['targets']
            report['browser_point_save_and_device_export_match']=True
            page.click('#tab-check');page.fill('#actualX','2.5');page.fill('#actualY','2.5');page.click('#addCheck')
            wait(lambda s:s['calibration']['validation']['count']==1)
            report['browser_independent_check_saved']=True
            page.click('#tab-detect');report['browser_frame_rates']={}
            for mode in ['off','yolo11','red_blob','apriltag','off']:
                page.select_option('#detectionMode',mode);page.click('#applyMode')
                wait(lambda s:s['device']['connected'] and s['device']['metadata'].get('mode')==mode)
                page.wait_for_timeout(800)
                if mode in ('off','yolo11'):
                    first=page.evaluate('[window.__frameIds.size,window.__decodedCount]');start=time.monotonic()
                    page.wait_for_timeout(5000)
                    last=page.evaluate('[window.__frameIds.size,window.__decodedCount]');elapsed=time.monotonic()-start
                    assert last[0]>first[0] and last[1]>first[1]
                    report['browser_frame_rates'][mode]={'seconds':round(elapsed,2),
                        'distinct_frames_received_fps':round((last[0]-first[0])/elapsed,2),
                        'images_decoded_and_drawn_fps':round((last[1]-first[1])/elapsed,2)}
            report['all_four_detection_modes_from_browser']=True
            page.locator('[data-remove]').click();wait(lambda s:not s['targets'])
            report['browser_target_deletion_saved']=True
            page.click('#disconnect');wait(lambda s:not s['device']['connected'])
            page.click('#connect');wait(lambda s:s['device']['connected'] and bool(s['device']['metadata']) and bool(s['calibration']))
            assert api('/api/status')['calibration']['created_at']==own_cal
            report['detach_and_reattach_preserves_device_calibration']=True
            page.screenshot(path=str(ROOT/'data/qa/pc-workflow-2.1.png'),full_page=True)
            browser.close();assert not report['errors']
            report['passed']=True
    finally:
        if own_cal is not None:
            current=api('/api/status')['calibration']
            if current and current['created_at']==own_cal:
                api('/api/calibration/clear',{})
                report['own_test_calibration_removed']=True
            else:report['passed']=False;report['cleanup_error']='Calibration changed during test; retained current data'
        (ROOT/'docs/pc-workflow-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
