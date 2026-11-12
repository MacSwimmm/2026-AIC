"""Real connect/disconnect UI cycles; leaves camera connected, saves no photos."""
import hashlib
import json
import sys
import time
import traceback
import urllib.request
from datetime import datetime,timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from device import ssh_connect,remote


def main():
    url='http://127.0.0.1:29860'
    op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def status():
        with op.open(url+'/api/status',timeout=3) as r:return json.load(r)
    initial=status();report={'tested_at_utc':datetime.now(timezone.utc).isoformat(),
        'test_type':'Windows EXE with real USB camera; browser connect/disconnect cycles',
        'application_version':initial['version'],'physical_ground_calibration_created':False,
        'exe_sha256':hashlib.sha256((ROOT/'MaixVisionConsole.exe').read_bytes()).hexdigest(),
        'cycles':[],'browser_errors':[],'passed':False}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1050})
            page.on('pageerror',lambda e:report['browser_errors'].append(str(e)))
            page.goto(url,wait_until='domcontentloaded')
            for index in range(3):
                page.wait_for_function('!document.querySelector("#connect").disabled')
                start=time.monotonic()
                with page.expect_response(lambda r:r.url==url+'/api/connect') as response:
                    page.click('#connect')
                assert response.value.ok
                deadline=time.monotonic()+65
                while time.monotonic()<deadline:
                    device=status()['device']
                    if device['connected'] and 'seq' in device['metadata']:break
                    if not device['busy']:raise RuntimeError(device['message'])
                    time.sleep(.2)
                else:raise RuntimeError('connection did not finish')
                page.wait_for_function('!document.querySelector("#disconnect").disabled && document.querySelector("#cameraEmpty").hidden',timeout=65000)
                first=status()['device']['metadata'];page.wait_for_timeout(1500)
                current=status()['device']
                assert current['connected'] and current['metadata']['seq']>first['seq']
                record={'cycle':index+1,'connect_and_live_seconds':round(time.monotonic()-start,2),'real_image_displayed':True,'sequence_advanced':True}
                report['cycles'].append(record);print(record,flush=True)
                if index<2:
                    page.click('#disconnect');page.wait_for_function('document.querySelector("#disconnect").disabled',timeout=15000)
                    assert page.locator('#saveTarget').is_disabled()
                    # Verify precisely one startup supervisor and one original
                    # startup application after native process exit.
                    c=ssh_connect(current['host'],'root','root',ROOT/'data'/'known_hosts')
                    try:
                        deadline=time.monotonic()+15
                        while time.monotonic()<deadline:
                            command="python3 -c \"import sys;sys.path.insert(0,'/root/aic_vision');import vision_agent as a;print(len(a.stock_processes(True)),len(a.stock_processes()))\""
                            counts=remote(c,command,timeout=10).strip()
                            if counts=='1 2':break
                            time.sleep(.3)
                        assert counts=='1 2',counts
                    finally:c.close()
                    record['single_startup_ui_restored']=True
                print('cycle complete',record,flush=True)
            # Exercise ordinary UI controls without creating a false field calibration.
            page.click('#freeze');page.wait_for_timeout(400);page.click('#freeze')
            page.click('#tab-detect');page.select_option('#detectionMode','red_blob');page.click('#applyMode')
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                if status()['device']['metadata'].get('mode')=='red_blob':break
                time.sleep(.2)
            else:raise RuntimeError('browser mode switch did not complete')
            page.select_option('#detectionMode','off');page.click('#applyMode');page.click('#tab-calibrate')
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                final=status()
                if final['device']['connected'] and final['device']['metadata'].get('mode')=='off':break
                time.sleep(.2)
            else:raise RuntimeError('off mode not restored')
            report['calibration_untouched']=initial['calibration']==final['calibration']
            report['final_mode']='off';assert report['calibration_untouched'] and not report['browser_errors']
            report['passed']=True;browser.close()
    except Exception as e:
        traceback.print_exc()
        report['error']=f'{type(e).__name__}: {e}'
    (ROOT/'docs'/'live-browser-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
