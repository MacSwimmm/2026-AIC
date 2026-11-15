"""Compare ordinary NV21 and detector RGB framing in one static live scene.

Keep the camera and scene still. This checks image registration, not field accuracy.
"""
import base64
import io
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from device import ssh_connect, remote


def main():
    c = ssh_connect('10.191.17.1', 'root', 'root', ROOT / 'data/known_hosts')
    try:
        cfg = json.loads(remote(c, 'cat /root/aic_vision/agent_config.json'))
    finally:
        c.close()
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, body=None):
        return op.open(urllib.request.Request('http://10.191.17.1:18765' + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + cfg['token'], 'Content-Type': 'application/json'}), timeout=5)

    def api(path, body=None):
        with request(path, body) as r:
            return json.load(r)

    original = api('/status')['mode']
    frames = []
    try:
        for mode in ('off', 'red_blob'):
            api('/mode', {'mode': mode})
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    if api('/status').get('mode') == mode:
                        break
                except OSError:
                    pass
                time.sleep(.2)
            time.sleep(3)
            with request('/frame.jpg') as r:
                raw = r.read()
                meta = json.loads(base64.b64decode(r.headers['X-Frame-Meta']))
            assert meta['mode'] == mode
            frames.append((cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE), meta))
        (a, ma), (b, mb) = frames
        sift = cv2.SIFT_create()
        ka, da = sift.detectAndCompute(a, None)
        kb, db = sift.detectAndCompute(b, None)
        pairs = cv2.BFMatcher().knnMatch(da, db, k=2)
        good = [m for m, n in pairs if m.distance < .7 * n.distance]
        pa = np.float32([ka[m.queryIdx].pt for m in good])
        pb = np.float32([kb[m.trainIdx].pt for m in good])
        matrix, mask = cv2.findHomography(pa, pb, cv2.RANSAC, 2.)
        inliers = mask.ravel().astype(bool)
        offsets = pb[inliers] - pa[inliers]
        displacement = np.linalg.norm(offsets, axis=1)
        result = {'tested_at_utc': datetime.now(timezone.utc).isoformat(),
            'test_type': 'Static scene RGB/NV21 JPEG feature registration; no physical accuracy measurement',
            'geometry_ids': [ma['geometry_id'], mb['geometry_id']],
            'pixel_formats': [ma['pixel_format'], mb['pixel_format']],
            'matches': len(good), 'inliers': int(inliers.sum()),
            'median_offset_xy_px': np.median(offsets, axis=0).tolist(),
            'median_displacement_px': float(np.median(displacement)),
            'p95_displacement_px': float(np.percentile(displacement, 95)),
            'field_accuracy_tested': False}
        result['passed'] = bool(len(offsets) >= 50 and np.median(displacement) < 1 and
                                np.percentile(displacement, 95) < 2 and ma['geometry_id'] == mb['geometry_id'])
        (ROOT / 'docs/capture-geometry-validation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result), flush=True)
        assert result['passed'], 'Image registration failed; check scene stability and camera framing'
    finally:
        api('/mode', {'mode': original})


if __name__ == '__main__':
    main()
