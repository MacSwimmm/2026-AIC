"""Real device test with injected Linux touchscreen events, not field accuracy."""
import argparse
import subprocess
import base64
import io
import hashlib
import json
import shlex
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from device import ssh_connect, remote

parser=argparse.ArgumentParser()
parser.add_argument('--performance',action='store_true')
args=parser.parse_args()

c = ssh_connect('10.191.17.1', 'root', 'root', ROOT / 'data' / 'known_hosts')
config = json.loads(remote(c, 'cat /root/aic_vision/agent_config.json'))
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
report = {'tested_at_utc': datetime.now(timezone.utc).isoformat(), 'version': '2.1.0',
          'native_package_sha256': hashlib.sha256((ROOT/'MaixGround-2.1.0.zip').read_bytes()).hexdigest(),
          'test_type': 'Real device native screen and injected Linux touchscreen events; synthetic coordinates',
          'field_accuracy_tested': False, 'physical_finger_touch_tested': False, 'passed': False}


def api(path, body=None):
    req = urllib.request.Request('http://10.191.17.1:18765' + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Bearer ' + config['token'], 'Content-Type': 'application/json'})
    try:
        with op.open(req, timeout=8) as r: return json.load(r)
    except urllib.error.HTTPError as e: raise RuntimeError(e.read().decode()) from e


def live(mode='off', timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = api('/status')
            if state['ready'] and state['mode'] == mode:
                r = op.open(urllib.request.Request('http://10.191.17.1:18765/frame.jpg',
                    headers={'Authorization': 'Bearer ' + config['token']}), timeout=4)
                data = r.read(); meta = json.loads(base64.b64decode(r.headers['X-Frame-Meta']))
                image = Image.open(io.BytesIO(data)); image.load()
                if meta['mode'] == mode and image.size == (640, 480): return meta
        except (OSError, ValueError, KeyError): pass
        time.sleep(.2)
    raise RuntimeError('No new frame: ' + mode)


def taps(points):
    code = '''import os,struct,time
fd=os.open('/dev/input/event1',os.O_WRONLY)
def emit(t,c,v):os.write(fd,struct.pack('llHHi',0,0,t,c,v))
for x,y in POINTS:
 for t,c,v in [(3,53,(y+1)%480),(3,54,(638-x)%640),(0,0,0)]:emit(t,c,v)
 for t,c,v in [(3,47,0),(3,57,1),(3,53,y),(3,54,639-x),(1,330,1),(0,0,0)]:emit(t,c,v)
 time.sleep(.35)
 for t,c,v in [(3,57,-1),(1,330,0),(0,0,0)]:emit(t,c,v)
 time.sleep(.4)
os.close(fd)
'''.replace('POINTS', repr(points))
    remote(c, '/usr/bin/python3 -c ' + shlex.quote(code), timeout=30)


def screenshot(name):
    time.sleep(1.1)
    with op.open(urllib.request.Request('http://10.191.17.1:18765/screen.jpg',
            headers={'Authorization': 'Bearer ' + config['token']}), timeout=4) as r: raw = r.read()
    (ROOT / 'data' / 'qa' / name).write_bytes(raw)
    im = Image.open(io.BytesIO(raw)); im.load(); assert im.size == (640, 480)


def restart():
    code = "import sys;sys.path.insert(0,'/root/aic_vision');from vision_agent import prepare_device;prepare_device('/root/aic_vision')"
    remote(c, '/usr/bin/python3 -c ' + shlex.quote(code), timeout=25)
    remote(c, 'cd /maixapp/apps/aic_ground && nohup /usr/bin/python3 -u main.py >/root/aic_vision/agent.log 2>&1 </dev/null &', timeout=5)
    return live()


def launcher_relaunch():
    # Ask the real supervisor to launch our registered menu entry when the
    # current stock application exits. This exercises its Python entry/env.
    code = "from maix import app;app.switch_app('aic_ground')"
    remote(c, '/usr/bin/python3 -c ' + shlex.quote(code), timeout=12)
    code = "import sys;sys.path.insert(0,'/root/aic_vision');import vision_agent as a; a.stop_processes([p for p in a.stock_processes() if p not in a.stock_processes(True)])"
    remote(c, '/usr/bin/python3 -c ' + shlex.quote(code), timeout=12)
    return live()


may_clear_test_data = False
try:
    restart()
    initial = api('/ground/status')
    assert initial['calibration'] is None and not initial['targets'], 'Do not overwrite user calibration'
    may_clear_test_data = True
    live()
    taps([(565, 50)])
    screenshot('native-size-final.jpg')
    taps([(60, 155), (400, 85), (60, 155), (565, 155)])
    # Map synthetic image corners to the actual native preview rectangle.
    taps([(47, 360), (453, 360), (312, 102), (187, 102)])
    screenshot('native-corners-final.jpg')
    taps([(565, 140)])
    ground = api('/ground/status')
    assert ground['calibration']['width_m'] == ground['calibration']['height_m'] == 1
    report['touchscreen_size_entry_and_four_point_calibration'] = True
    taps([(250, 163), (565, 95)])
    targets = api('/api/targets/export')
    assert len(targets['targets']) == 1
    p = targets['targets'][0]
    assert abs(p['x'] - .5) < .01 and abs(p['y'] - .5) < .02, p
    screenshot('native-target-final.jpg')
    report['touchscreen_selection_and_durable_target_save'] = True
    disk = json.loads(remote(c, 'cat /root/aic_vision/data/ground.json'))
    assert disk['targets'] == targets['targets']
    report['device_disk_matches_export'] = True
    if args.performance:
        subprocess.run([sys.executable,'-u',str(ROOT/'tools/check_performance.py'),'--seconds','15','--output',str(ROOT/'docs/performance-calibrated-pc.json')],check=True,cwd=ROOT)
    restart()
    assert api('/api/targets/export') == targets
    report['installed_application_restart_preserves_data'] = True
    # Exit from the screen: must release resources before restoring stock UI.
    taps([(565, 273)])
    time.sleep(3)
    counts = remote(c, "python3 -c \"import sys;sys.path.insert(0,'/root/aic_vision');import vision_agent as a;print(len(a.stock_processes(True)),len(a.stock_processes()))\"", timeout=10).strip()
    assert counts == '1 2', counts
    report['screen_exit_restores_single_stock_ui'] = True
    launcher_relaunch()
    assert api('/api/targets/export') == targets
    report['exit_and_relaunch_preserves_target_batch'] = True
    report['system_launcher_menu_entry_started_application'] = True
    screenshot('native-after-relaunch.jpg')
    taps([(565,185)])
    screenshot('native-mode-picker.jpg')
    taps([(320,175)])
    live('yolo11')
    taps([(565,185),(320,110)])
    live('off')
    report['explicit_touchscreen_yolo_picker_both_directions']=True
    taps([(565,317)])
    screenshot('native-network-help.jpg')
    taps([(440,330)])
    report['passed'] = True
finally:
    try:
        if not may_clear_test_data: raise RuntimeError('未创建测试数据，保留原数据')
        live(); api('/api/calibration/clear', {})
        final = api('/ground/status')
        assert final['calibration'] is None and not final['targets']
        report['synthetic_test_calibration_and_target_removed'] = True
    except Exception as e: report['cleanup_error'] = str(e); report['passed'] = False
    c.close()
    (ROOT / 'docs' / 'native-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
