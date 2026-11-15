"""Measure real capture progress and decoded, distinct PC frames without changing calibration.

Use --standalone to measure the screen with no JPEG subscriber. Run other
viewers closed for that measurement. Wi-Fi is tested only when --host is the
camera's Wi-Fi address; USB measurements do not establish wireless throughput.
"""
import argparse
import base64
import hashlib
import io
import json
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from device import ssh_connect, remote, read_mjpeg


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', default='10.191.17.1')
    p.add_argument('--seconds', type=float, default=20)
    p.add_argument('--standalone', action='store_true')
    p.add_argument('--modes', nargs='+', default=['off', 'yolo11'])
    p.add_argument('--output', type=Path, default=ROOT / 'docs' / 'performance-validation.json')
    args = p.parse_args()
    c = ssh_connect(args.host, 'root', 'root', ROOT / 'data' / 'known_hosts')
    try:
        cfg = json.loads(remote(c, 'cat /root/aic_vision/agent_config.json'))
        sftp=c.open_sftp();source_hashes={}
        for name in ('vision_agent.py','native_ui.py'):
            with sftp.open('/root/aic_vision/'+name,'rb') as f:source_hashes['device/'+name]=hashlib.sha256(f.read()).hexdigest()
        sftp.close()
    finally: c.close()
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, body=None):
        return op.open(urllib.request.Request('http://' + args.host + ':18765' + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + cfg['token'], 'Content-Type': 'application/json'}), timeout=4)

    def api(path, body=None):
        with request(path, body) as r: return json.load(r)

    def wait_mode(mode):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                s = api('/status')
                if s.get('ready') and s.get('mode') == mode: return s
            except OSError: pass
            time.sleep(.2)
        raise RuntimeError('mode failed: ' + mode)

    before = api('/ground/status')
    initial = api('/status');original_mode = initial['mode']
    report = {'tested_at_utc': datetime.now(timezone.utc).isoformat(), 'host': args.host,
        'application_version':initial['version'],
        'viewer': 'standalone screen' if args.standalone else 'PC decoded JPEG; native preview policy follows application version',
        'resolution': [640, 480], 'yolo_model': 'yolo11n_320x224_int8',
        'confidence': .5, 'iou': .45, 'passed': False, 'modes': {},
        'physical_accuracy_tested': False,
        'source_sha256':source_hashes}
    try:
        for mode in args.modes:
            api('/mode', {'mode': mode}); wait_mode(mode); time.sleep(3)
            stream=None if args.standalone else request('/stream.mjpg')
            first = api('/status'); start = time.monotonic()
            identities = set(); sizes = []; samples = []; last = ''; max_gap = 0; last_time = start
            while time.monotonic() - start < args.seconds:
                if args.standalone:
                    samples.append(api('/status')); time.sleep(.15)
                else:
                    jpeg,meta=read_mjpeg(stream)
                    image = Image.open(io.BytesIO(jpeg)); image.load()
                    assert image.size == (640, 480) and meta['mode'] == mode
                    last = f"{meta['session_id']}:{meta['seq']}"
                    if last not in identities:
                        identities.add(last); sizes.append(len(jpeg))
                        now = time.monotonic(); max_gap = max(max_gap, now-last_time); last_time = now
                    samples.append(meta)
            if stream:stream.close()
            elapsed = time.monotonic() - start; final = api('/status')
            assert final['ready'] and final['seq'] > first['seq']
            result = {'seconds': round(elapsed, 2),
                'capture_fps_measured': round((final['seq']-first['seq'])/elapsed, 2),
                'capture_fps_reported_median': statistics.median(s['fps'] for s in samples if 'fps' in s) if any('fps' in s for s in samples) else None,
                'screen_fps_reported': final.get('screen_fps'),
                'screen_redraws':final.get('display_seq',0)-first.get('display_seq',0),
                'local_preview_frames':final['local_preview_seq']-first['local_preview_seq'] if 'local_preview_seq' in first else None,
                'distinct_pc_frames': len(identities),
                'pc_fps_measured': round(len(identities)/elapsed, 2) if identities else None,
                'max_pc_frame_gap_s': round(max_gap, 3) if identities else None,
                'mean_jpeg_bytes': round(statistics.mean(sizes)) if sizes else None,
                'detections_max': max((s.get('detection_count',len(s.get('detections', []))) for s in samples), default=0),
                'timings_ms_median': {k: round(statistics.median(s['timings_ms'][k] for s in samples), 2)
                                      for k in samples[-1].get('timings_ms', {})}}
            report['modes'][mode] = result
            print(mode, json.dumps(result), flush=True)
        after = api('/ground/status')
        report['calibration_and_targets_unchanged'] = all(before[k] == after[k]
                                                       for k in ('calibration', 'targets', 'revision'))
        assert report['calibration_and_targets_unchanged']
        report['passed'] = True
    finally:
        api('/mode', {'mode': original_mode}); wait_mode(original_mode)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__': sys.exit(main())
