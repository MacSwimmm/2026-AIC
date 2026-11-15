"""Raspberry Pi HTTP receiver: saves a batch and ACKs it; does not move a vehicle."""
import argparse
import json
import math
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class Receiver(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_POST(self):
        try:
            if self.path != '/targets': raise ValueError('Unknown endpoint')
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 1000000: raise ValueError('Invalid batch size')
            batch = json.loads(self.rfile.read(length))
            if batch['type'] != 'ground_targets' or batch['frame'] != 'ground' or batch['units'] != 'm':
                raise ValueError('Unsupported coordinates')
            if not batch['request_id'] or not batch['device_id'] or not isinstance(batch['revision'], int):
                raise ValueError('Missing batch identity')
            for target in batch['targets']:
                if any(isinstance(target[k], bool) or not math.isfinite(float(target[k])) for k in ('x', 'y')):
                    raise ValueError('Invalid coordinate')
            target = self.server.output
            temp = target.with_suffix('.tmp')
            with temp.open('w', encoding='utf-8') as f:
                json.dump(batch, f, ensure_ascii=False, indent=2, allow_nan=False)
                f.flush(); os.fsync(f.fileno())
            os.replace(temp, target)
            result = {'accepted': True, 'request_id': batch['request_id'], 'revision': batch['revision']}
            status = 200
        except (ValueError, KeyError, TypeError, OSError) as e:
            result = {'accepted': False, 'error': str(e)}; status = 400
        body = json.dumps(result).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--bind', default='0.0.0.0')
    p.add_argument('--port', type=int, default=18766)
    p.add_argument('--output', type=Path, default=Path('ground-targets.json'))
    args = p.parse_args()
    server = HTTPServer((args.bind, args.port), Receiver)
    server.output = args.output; args.output.parent.mkdir(parents=True, exist_ok=True)
    print(f'Target receiver on {args.bind}:{args.port}/targets', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__': main()
