"""Browser interactions against an isolated backend and synthetic ground image.

Never connects to hardware and never edits the user's calibration. Run manually:
python tests/browser_check.py (requires playwright, Pillow, and Microsoft Edge).
"""
import base64
import io
import json
import re
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"host"))
from server import Application,Handler
from geometry import Calibration


def fixture_image():
    img=Image.new('RGB',(640,480),(15,20,24));d=ImageDraw.Draw(img)
    c=Calibration({'width_m':5,'height_m':5,'image_size':[640,480],'points':[[60,420],[580,420],[400,90],[240,90]]})
    d.polygon([tuple(p) for p in c.points],fill=(34,47,39))
    for line in c.grid():d.line([tuple(p) for p in line['points']],fill=(64,97,74),width=1)
    d.text((20,25),'SIMULATED FLOOR / UI TEST ONLY',fill=(212,227,217))
    for label,p in zip('ABCD',c.points):
        x,y=p;d.ellipse((x-5,y-5,x+5,y+5),fill=(236,210,148));d.text((x+10,y-8),label,fill=(236,210,148))
    out=io.BytesIO();img.save(out,'JPEG',quality=90);return out.getvalue()


class FakeDevice:
    connected=True
    def __init__(self):self.frame=fixture_image();self.seq=0
    def snapshot(self,fresh=True):
        if not self.connected:raise ValueError('相机画面已中断，请重新连接')
        self.seq+=1
        return self.frame,{'seq':self.seq,'received_at':time.time(),'captured_at':time.time(),'device_id':'synthetic-test-only','geometry_id':'640x480-rgb-v1','width':640,'height':480,'mode':'off','fps':10,'detections':[]}
    def status(self):
        _,meta=self.snapshot() if self.connected else (None,{})
        return {'connected':self.connected,'busy':False,'metadata':meta,'host':'synthetic-test-only','message':'模拟地面 / 浏览器功能验证','mode':'off'}


def main():
    reports={}
    with tempfile.TemporaryDirectory() as temp:
        app=Application(temp);app.device=FakeDevice()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True;server.app=app
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as s:
                browser=s.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1050},device_scale_factor=2)
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.server_port}',wait_until='domcontentloaded')
                page.wait_for_function('document.querySelector("#cameraEmpty").hidden')
                page.click('#startCalibrate')
                def click_pixel(x,y):
                    box=page.locator('#cameraCanvas').bounding_box()
                    page.mouse.click(box['x']+x/640*box['width'],box['y']+y/480*box['height'])
                for p in [[60,420],[580,420],[400,90],[240,90]]:click_pixel(*p)
                page.click('#applyCalibration')
                page.wait_for_function('document.querySelector("#calibrationInfo").textContent.includes("5 × 5")')
                click_pixel(320,167.64705882352942)
                page.wait_for_function('!document.querySelector("#saveTarget").disabled')
                coords=[float(x) for x in re.findall(r'\d+\.\d+',page.locator('#coordinate').inner_text())]
                # Real mouse clicks are rounded to CSS pixels by the browser.
                assert len(coords)==2 and max(abs(v-2.5) for v in coords)<.04,coords
                reports['desktop_pixel_to_world_at_dpr2']='passed'
                page.click('#saveTarget');page.wait_for_function('document.querySelector("#targetCount").textContent.includes("1 个")')
                reports['save_target']='passed'
                page.click('#tab-check');page.fill('#actualX','2.5');page.fill('#actualY','2.5');page.click('#addCheck')
                page.wait_for_function('document.querySelector("#validationResults").textContent.includes("1 个独立")')
                assert app.cal.export()['validation']['rmse_m']<.05
                reports['synthetic_validation_workflow']='passed (not field accuracy)'
                page.click('#tab-calibrate')
                page.screenshot(path=str(ROOT/'docs'/'ui-desktop.png'),full_page=True)
                click_pixel(10,10)
                page.wait_for_function('document.querySelector("#message").textContent.includes("区域外")')
                assert page.locator('#saveTarget').is_disabled()
                reports['outside_click_clears_previous_selection']='passed'
                requests=[];page.on('request',lambda r:requests.append(r.url) if '/api/frame.jpg' in r.url else None)
                page.click('#freeze');time.sleep(.25);count=len(requests);page.wait_for_timeout(700)
                assert len(requests)==count
                page.click('#freeze');reports['freeze_stops_frame_requests']='passed'
                page.set_viewport_size({'width':390,'height':844});page.goto(page.url,wait_until='domcontentloaded')
                page.wait_for_function('document.querySelector("#cameraEmpty").hidden')
                click_pixel(320,167.64705882352942)
                page.wait_for_function('!document.querySelector("#saveTarget").disabled')
                coords=[float(x) for x in re.findall(r'\d+\.\d+',page.locator('#coordinate').inner_text())]
                assert len(coords)==2 and max(abs(v-2.5) for v in coords)<.05,coords
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                reports['mobile_scaled_canvas_mapping']='passed'
                page.screenshot(path=str(ROOT/'docs'/'ui-mobile.png'),full_page=True)
                app.device.connected=False
                page.wait_for_function('document.querySelector("#coordinate").textContent.includes("X —")')
                assert page.locator('#saveTarget').is_disabled()
                reports['disconnect_disables_coordinate_outputs']='passed'
                reports['browser_errors']=errors;assert not errors
                browser.close()
        finally:server.shutdown();server.server_close()
    reports['image_source']='synthetic, no live camera photograph'
    (ROOT/'docs'/'browser-validation.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(reports,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
