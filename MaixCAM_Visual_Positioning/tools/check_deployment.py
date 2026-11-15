"""Read back installed/shared sources and verify this session's saved user data.

The original-data check requires the private fps60-original backup on this device.
No credentials, scene pixels or device identifiers are included in the report.
"""
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from device import ssh_connect, remote


def main():
    c = ssh_connect('10.191.17.1', 'root', 'root', ROOT / 'data/known_hosts')
    sftp = c.open_sftp()
    try:
        def read(path):
            with sftp.open(path, 'rb') as f:
                return f.read()

        cfg = json.loads(read('/root/aic_vision/agent_config.json'))
        original = json.loads(read('/root/aic_vision/fps60-original/agent_config.json'))
        hashes = {}
        shared = ('vision_agent.py', 'native_ui.py', 'ground.py', 'pixel_convert.py', '_pixel_ops.so', 'geometry.py')
        for name in (*shared, 'main.py', 'app.yaml', 'icon.png'):
            local = ROOT / ('host' if name == 'geometry.py' else 'device') / name
            expected = local.read_bytes()
            assert read('/maixapp/apps/aic_ground/' + name) == expected, 'Installed mismatch: ' + name
            if name in shared:
                assert read('/root/aic_vision/' + name) == expected, 'Shared mismatch: ' + name
            hashes[name] = hashlib.sha256(expected).hexdigest()
        user_data = {}
        for name in ('ground.json', 'pi.json'):
            actual = read('/root/aic_vision/data/' + name)
            assert actual == read('/root/aic_vision/fps60-original/data/' + name), 'User data mismatch: ' + name
            user_data[name] = hashlib.sha256(actual).hexdigest()
        assert cfg['token'] == original['token'], 'Original credential changed'
        temperature = float(remote(c, 'cat /sys/class/thermal/thermal_zone0/temp')) / 1000
        models = {}
        for name in ('yolo11n.mud', 'yolo11n_320x224_int8.cvimodel'):
            models[name] = hashlib.sha256(read('/root/models/' + name)).hexdigest()
    finally:
        sftp.close()
        c.close()
    req = urllib.request.Request('http://10.191.17.1:18765/status', headers={'Authorization': 'Bearer ' + cfg['token']})
    with urllib.request.urlopen(req, timeout=5) as r:
        state = json.load(r)
    assert state['ready'] and state['mode'] == 'off'
    assert state['fps_limit'] == 45 and state['camera_fps'] == 60 and state['pixel_format'] == 'nv21'
    result = {'tested_at_utc': datetime.now(timezone.utc).isoformat(), 'version': state['version'],
        'installed_and_shared_equal_source': True, 'source_sha256': hashes,
        'original_ground_and_pi_bytes_restored': True, 'user_data_sha256': user_data,
        'original_private_token_preserved': True, 'model_sha256': models,
        'camera_fps': state['camera_fps'], 'fps_limit': state['fps_limit'],
        'geometry_id': state['geometry_id'], 'pixel_format': state['pixel_format'],
        'temperature_c': round(temperature, 2), 'ready': True, 'mode': 'off', 'passed': True}
    (ROOT / 'docs/deployment-validation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
