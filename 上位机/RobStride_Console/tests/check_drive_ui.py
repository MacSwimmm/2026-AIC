"""Browser WASD regression with intercepted telemetry; never connects a probe."""
import argparse
import json
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from profile import decode
from server import DEFAULTS,LIMITS


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8876')
    parser.add_argument('--output',type=Path,default=ROOT/'review/wasd')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    profile=json.loads((ROOT/'firmware_profile.json').read_text('utf-8'))
    snap=decode(profile['schema'],bytes(profile['schema']['size']))
    snap.update(coherent=True,coherent2=True)
    snap['setup']['dual_ready']=1
    for name in ('motor','motor2'):
        snap[name].update(state=2,online=1,enabled=1,enable_requested=1,feedback_age_ms=0,
                          timeout_motor_supported=1,**DEFAULTS)
    status=dict(connected=True,snapshot=snap,sample_age_ms=0,motor_count=2,
        pending=None,pending_motors=[None,None],setup_pending=None,continuous=False,
        continuous_motors=[False,False],led_supported=True,drive_supported=True,drive_active=False,
        profile={'source':'Browser fixture; no hardware','axf_sha256':'fixture'},
        history_count=0,logs=[],probes=[],defaults=DEFAULTS,limits=LIMITS)
    submitted=[];errors=[];checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1500,'height':1100},reduced_motion='reduce')
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route(r):
            action=r.request.url.rsplit('/',1)[-1]
            if action=='status':
                r.fulfill(json=status);return
            if action=='session':r.fulfill(json={'token':'fixture'});return
            payload=r.request.post_data_json
            submitted.append((action,payload))
            result={}
            if action=='drive_enter':status['drive_active']=True;snap['drive']['active']=1;result={'owner':'owner'}
            elif action in ('drive_exit','stop_all'):
                status['drive_active']=False;snap['drive']['active']=0
            elif action=='probes':result=[]
            r.fulfill(json={'ok':True,'data':result})
        page.route('**/api/*',route)
        page.goto(args.url)
        page.wait_for_function('() => data?.connected && !busy')
        page.click('#drive-panel summary')
        page.check('#confirmed');page.click('#select-motor-1');page.check('#confirmed')
        page.fill('#drive-speed','33')
        def enter():
            page.click('#drive-enter')
            expect(page.locator('#drive-exit')).to_be_enabled()
        def vector(value):
            for _ in range(40):
                if submitted and any(a=='drive_update' and all(abs(x-y)<1e-6 for x,y in zip(v['targets'],value)) for a,v in submitted):return
                page.wait_for_timeout(25)
            raise AssertionError((value,submitted[-10:]))
        def key(code,wanted):
            submitted.clear();page.keyboard.down(code);vector(wanted)
        enter()
        expect(page.locator('#run')).to_be_disabled()
        key('w',[33,-33]);submitted.clear();page.keyboard.up('w');vector([0,0])
        key('s',[-33,33]);page.keyboard.up('s')
        key('a',[-33,-33]);page.keyboard.up('a')
        key('d',[33,33]);page.keyboard.up('d')
        page.keyboard.down('w');key('d',[33,0]);page.keyboard.up('d');page.keyboard.up('w')
        checks.append('W/S/A/D and diagonal mapping retain full existing 33 rad/s range; keyup zeros both')
        key('w',[33,-33]);submitted.clear();page.keyboard.press('Space');vector([0,0])
        page.keyboard.up('w')
        checks.append('Space clears held vector')
        page.click('#open-settings');submitted.clear()
        page.keyboard.press('w');page.wait_for_timeout(220)
        assert not any(a=='drive_update' and any(v['targets']) for a,v in submitted)
        page.click('#close-settings')
        key('w',[33,-33]);submitted.clear();page.evaluate("window.dispatchEvent(new Event('blur'))")
        expect(page.locator('#drive-enter')).to_be_enabled()
        assert any(a=='drive_exit' for a,_ in submitted)
        page.keyboard.up('w')
        checks.append('editing/settings ignore movement keys; window blur releases the session')
        enter();page.click('#tab-led')
        page.wait_for_function('() => !data.drive_active')
        assert submitted[-1][0]=='drive_exit'
        page.click('#tab-control');enter();page.keyboard.press('Escape')
        page.wait_for_function('() => !data.drive_active')
        assert any(a=='stop_all' for a,_ in submitted)
        checks.append('module switch releases; original Esc all-disable stays available')
        page.select_option('#drive-left','1');page.select_option('#drive-left-sign','-1');page.select_option('#drive-right-sign','1')
        enter();key('w',[33,-33]);page.keyboard.up('w')
        page.click('#drive-exit')
        checks.append('left/right motor assignment and mounting polarity are configurable')
        page.locator('#drive-panel').scroll_into_view_if_needed()
        page.screenshot(path=str(args.output/'drive-desktop.png'))
        page.set_viewport_size({'width':390,'height':1000})
        page.locator('#drive-panel').scroll_into_view_if_needed()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.screenshot(path=str(args.output/'drive-mobile.png'))
        assert not errors,errors
        browser.close()
    (args.output/'checks.json').write_text(json.dumps({'passed':True,'checks':checks,'errors':errors,
        'physical_motion_tested':False},ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS WASD browser:',len(checks),'checks; no hardware connection')


if __name__=='__main__':main()
