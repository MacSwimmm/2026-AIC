"""Real MaixCAM HTTP sender to the sample receiver on this PC, not a real Pi.

Requires an uncalibrated device. Creates and removes only its synthetic test
calibration/target, and restores the previous receiver URL.
"""
import argparse
import base64
import hashlib
import json
import sys
import tempfile
import threading
import urllib.request
from datetime import datetime, timezone
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'examples'))
from device import ssh_connect, remote
from pi_target_receiver import Receiver


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--host', default='10.191.17.1')
    p.add_argument('--pc-usb-ip', default='10.191.17.100')
    p.add_argument('--port', type=int, default=18766)
    args = p.parse_args()
    c = ssh_connect(args.host, 'root', 'root', ROOT / 'data' / 'known_hosts')
    config = json.loads(remote(c, 'cat /root/aic_vision/agent_config.json'))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(path, body=None):
        req = urllib.request.Request(f'http://{args.host}:18765' + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + config['token'], 'Content-Type': 'application/json'})
        with opener.open(req, timeout=8) as response: return json.load(response)
    initial = api('/ground/status')
    if initial['calibration'] or initial['targets']:
        c.close(); raise RuntimeError('已有标定/目标点，拒绝覆盖；请用空白测试设备运行')
    report = {'tested_at_utc': datetime.now(timezone.utc).isoformat(), 'version': '2.2.0',
        'native_package_sha256': hashlib.sha256((ROOT/'MaixGround-2.2.0.zip').read_bytes()).hexdigest(),
        'test_type': 'Real MaixCAM API sends a synthetic target batch over USB network to PC sample receiver',
        'raspberry_pi_hardware_tested': False, 'vehicle_motion_tested': False, 'passed': False}
    previous_url = initial.get('pi', {}).get('url', '')
    modified = False
    try:
        with tempfile.TemporaryDirectory() as temp:
            server = HTTPServer((args.pc_usb_ip, args.port), Receiver)
            server.output = Path(temp) / 'received.json'
            worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
            try:
                api('/api/calibration', {'width_m': 1, 'height_m': 1,
                    'points': [[60, 420], [580, 420], [400, 90], [240, 90]]})
                modified = True
                api('/api/targets', {'world': [.5, .5], 'name': 'SYNTHETIC DELIVERY TEST'})
                batch = api('/api/targets/export')
                api('/api/pi/config', {'url': f'http://{args.pc_usb_ip}:{args.port}/targets'})
                ack = api('/api/pi/send', {})
                saved = json.loads(server.output.read_text(encoding='utf-8'))
                assert ack['accepted'] and ack['request_id'] == saved['request_id']
                assert ack['revision'] == saved['revision'] == batch['revision']
                assert batch['targets'] == saved['targets']
                assert saved['device_id'] == batch['device_id']
                assert saved['calibration_created_at'] == batch['calibration_created_at']
                report.update(api_triggered_device_send=True, network_receiver_saved_same_target_batch=True,
                    matching_request_id_and_revision_ack=True, target_count=len(saved['targets']), passed=True)
            finally:
                server.shutdown(); server.server_close(); worker.join(2)
    except Exception as e:
        report['error'] = f'{type(e).__name__}: {e}'
    finally:
        try:
            if modified:
                api('/api/pi/config', {'url': previous_url})
                api('/api/calibration/clear', {})
                final = api('/ground/status')
                assert not final['calibration'] and not final['targets']
                assert final['pi']['url'] == previous_url
                report['test_data_removed_and_receiver_url_restored'] = True
        except Exception as e:
            report['cleanup_error'] = str(e); report['passed'] = False
        c.close()
        (ROOT/'docs'/'pi-delivery-validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__': sys.exit(main())
