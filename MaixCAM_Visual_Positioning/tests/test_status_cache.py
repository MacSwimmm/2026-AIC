"""Conditional ground status must preserve every user-visible state change."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from test_pipeline import agent
from test_geometry import fixture
from ground import GroundStore, PiLink


class StatusCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ground, self.pi = GroundStore(self.temp.name), PiLink(self.temp.name)
        self.meta = {'device_id': 'test-camera', 'geometry_id': '640x480-rgb-v1',
                     'width': 640, 'height': 480, 'monotonic_at': time.monotonic()}
        self.globals = patch.multiple(agent, ground=self.ground, pi_link=self.pi,
                                      TOKEN='test-only', capture_meta=self.meta)
        self.globals.start()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), agent.Handler)
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()
        self.op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.worker.join(2)
        self.globals.stop(); self.temp.cleanup()

    def get(self, etag=None):
        req = urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}/ground/status',
            headers={'Authorization': 'Bearer test-only', **({'If-None-Match': etag} if etag else {})})
        try:
            with self.op.open(req) as r:
                return r.status, json.load(r), r.headers['ETag']
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers.get('ETag')

    def test_unchanged_frames_skip_grid_copy_but_keep_authorization(self):
        code, body, etag = self.get()
        self.assertEqual(code, 200)
        self.meta['seq'] = 100
        with patch.object(self.ground, 'status', side_effect=AssertionError('Unnecessary grid copy')):
            self.assertEqual(self.get(etag), (304, b'', etag))
        with patch.object(agent, 'TOKEN', 'different'):
            self.assertEqual(self.get(etag)[0], 401)

    def test_cache_invalidates_for_calibration_targets_pi_compatibility_and_errors(self):
        etag = self.get()[2]
        actions = [
            lambda: self.ground.post('/api/calibration', fixture(), self.meta),
            lambda: self.ground.post('/api/targets', {'world': [2.5, 2.5]}, self.meta),
            lambda: self.pi.configure({'url': 'http://127.0.0.1:18766/targets'}),
            lambda: setattr(self.pi, 'message', 'Send failed'),
            lambda: self.meta.update(geometry_id='640x480-sensor60-v2'),
            lambda: setattr(self.ground, 'load_error', 'Read error'),
            lambda: self.ground.post('/api/calibration/clear', {}, self.meta)
        ]
        for action in actions:
            action()
            code, body, updated = self.get(etag)
            self.assertEqual(code, 200)
            self.assertNotEqual(updated, etag)
            self.assertEqual(self.get(updated), (304, b'', updated))
            etag = updated
        self.assertIsNone(body['calibration'])
        self.assertEqual(body['targets'], [])


if __name__ == '__main__':
    unittest.main()
