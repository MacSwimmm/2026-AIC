import json
import sys
import tempfile
import time
import unittest
import threading
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'device'))
from ground import GroundStore, PiLink
from native_ui import Screen
from test_geometry import fixture


class GroundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GroundStore(self.temp.name)
        self.meta = {'width': 640, 'height': 480, 'device_id': 'test-device',
                     'geometry_id': '640x480-rgb-v1', 'monotonic_at': time.monotonic()}

    def tearDown(self): self.temp.cleanup()

    def calibrate(self): return self.store.post('/api/calibration', fixture(), self.meta)

    def test_no_implicit_import_of_old_pc_test_files(self):
        for name, obj in [('calibration.json', fixture()), ('targets.json', [{'x': 1, 'y': 2}])]:
            Path(self.temp.name, name).write_text(json.dumps(obj))
        self.assertIsNone(GroundStore(self.temp.name).cal)
        self.assertEqual(GroundStore(self.temp.name).targets, [])

    def test_device_reopen_preserves_calibration_targets_and_revision(self):
        cal = self.calibrate()
        target = self.store.post('/api/targets', {'world': [2.5, 2.5]}, self.meta)
        new = GroundStore(self.temp.name)
        exported = new.export_targets(self.meta)
        self.assertEqual(exported['targets'], [target])
        self.assertEqual(exported['revision'], 2)
        self.assertEqual(exported['calibration_created_at'], cal['created_at'])
        self.assertAlmostEqual(target['x'], 2.5)

    def test_failed_disk_write_does_not_acknowledge_or_change_live_state(self):
        self.calibrate()
        before = self.store.path.read_bytes()
        with patch('ground.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.post('/api/targets', {'world': [2, 2]}, self.meta)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.targets, [])
        self.assertEqual(self.store.revision, 1)

    def test_recalibration_atomically_replaces_old_coordinate_system_and_targets(self):
        self.calibrate(); self.store.post('/api/targets', {'world': [2, 2]}, self.meta)
        self.store.post('/api/calibration', {**fixture(), 'width_m': 2}, self.meta)
        new = GroundStore(self.temp.name)
        self.assertEqual(new.cal.width, 2)
        self.assertEqual(new.targets, [])

    def test_stale_frame_or_wrong_camera_cannot_add_target(self):
        self.calibrate()
        for changed in ({'monotonic_at': time.monotonic() - 4}, {'device_id': 'another'}):
            with self.assertRaises(ValueError):
                self.store.post('/api/targets', {'world': [1, 1]}, {**self.meta, **changed})

    def test_sensor_timing_change_preserves_but_does_not_reuse_old_coordinates(self):
        self.calibrate();self.store.post('/api/targets',{'world':[1,1]},self.meta)
        original=self.store.path.read_bytes()
        new_meta={**self.meta,'geometry_id':'640x480-sensor60-v2'}
        self.assertFalse(self.store.status(new_meta)['calibration_compatible'])
        self.assertEqual(self.store.status(new_meta)['targets'],[])
        with self.assertRaises(ValueError):self.store.export_targets(new_meta)
        self.assertEqual(self.store.path.read_bytes(),original)
        self.assertEqual(len(self.store.export_targets(self.meta)['targets']),1)

    def test_corrupt_store_is_retained_and_not_used(self):
        self.store.path.write_text('{damaged')
        new = GroundStore(self.temp.name)
        self.assertIsNone(new.cal); self.assertTrue(new.load_error)
        self.assertEqual(self.store.path.read_text(), '{damaged')

    def test_touch_mapping_and_save_use_same_device_coordinates(self):
        self.calibrate()
        ui = Screen.__new__(Screen)
        ui.store = self.store; ui.buttons = []; ui.page = 'live'; ui.vw = 400; ui.vh = 300
        ui.selection = None
        ui.remote_view = False
        ui.tap(200, 32 + 167.64705882352942 * 300 / 480, self.meta)
        self.assertAlmostEqual(ui.selection['x'], 2.5)
        self.assertAlmostEqual(ui.selection['y'], 2.5)
        ui.action('save', self.meta)
        self.assertEqual(len(GroundStore(self.temp.name).targets), 1)

    def test_explicit_mode_picker_selects_yolo_from_any_current_mode(self):
        from unittest.mock import Mock
        ui=Screen.__new__(Screen);ui.set_mode=Mock()
        ui.remote_view=False
        for active in ('off','red_blob','apriltag'):
            ui.action('mode',{'mode':active})
            self.assertEqual(ui.page,'modes')
            ui.action('mode:yolo11',{'mode':active})
            self.assertEqual(ui.page,'live')
        self.assertEqual(ui.set_mode.call_count,3)

    def test_touch_is_polled_without_waiting_for_a_screen_redraw(self):
        from unittest.mock import Mock
        ui=Screen.__new__(Screen);ui.store=self.store;ui.pressed=False;ui.dirty=False
        ui.drawn_status=None;ui.message='';ui.sending=False
        ui.touch=Mock();ui.touch.read.return_value=[565,185,True];ui.tap=Mock()
        ui.poll(self.meta)
        ui.tap.assert_called_once_with(565,185,self.meta)
        self.assertTrue(ui.dirty)

    def test_pc_connection_skips_camera_pixels_and_restores_native_preview(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        image=Mock()
        display=Mock();display.width.return_value=640;display.height.return_value=480
        maix=SimpleNamespace(image=image,display=SimpleNamespace(Display=lambda:display),
                             touchscreen=SimpleNamespace(TouchScreen=Mock))
        with patch.dict(sys.modules,{'maix':maix}):ui=Screen(self.store,Mock(),Mock())
        frame=Mock()
        ui.set_remote_view(True);ui.draw(frame,self.meta)
        frame.resize.assert_not_called()
        self.assertEqual(ui.preview_seq,0)
        ui.tap(250,160,self.meta)
        self.assertIsNone(ui.selection)
        ui.action('calibrate',self.meta)
        self.assertEqual(ui.page,'live')
        ui.set_remote_view(False);ui.draw(frame,self.meta)
        frame.resize.assert_called_once_with(ui.vw,ui.vh)
        self.assertEqual(ui.preview_seq,1)

    def test_controls_remain_static_until_touch_or_saved_state_changes(self):
        from unittest.mock import Mock
        ui=Screen.__new__(Screen);ui.store=self.store;ui.pressed=False;ui.dirty=False
        ui.message='ready';ui.sending=False;ui.touch=Mock();ui.touch.read.return_value=[0,0,False]
        ui.drawn_status=ui.status_key(self.meta)
        ui.poll(self.meta)
        self.assertFalse(ui.dirty)
        ui.message='send complete'
        ui.poll(self.meta)
        self.assertTrue(ui.dirty)

    def test_pi_delivery_saves_same_batch_and_requires_matching_ack(self):
        sys.path.insert(0, str(ROOT / 'examples'))
        from pi_target_receiver import Receiver
        self.calibrate(); self.store.post('/api/targets', {'world': [2, 2]}, self.meta)
        batch = self.store.export_targets(self.meta)
        server = HTTPServer(('127.0.0.1', 0), Receiver)
        server.output = Path(self.temp.name) / 'pi-received.json'
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            link = PiLink(self.temp.name)
            link.configure({'url': f'http://127.0.0.1:{server.server_port}/targets'})
            result = link.send(batch)
            saved = json.loads(server.output.read_text(encoding='utf-8'))
            self.assertEqual(saved['targets'], batch['targets'])
            self.assertEqual(saved['revision'], batch['revision'])
            self.assertEqual(saved['request_id'], result['request_id'])
            self.assertTrue(result['accepted'])
            self.assertFalse(result['vehicle_motion_verified'])
            self.assertEqual(PiLink(self.temp.name).url, link.url)
            import io
            fake = io.BytesIO(b'{"accepted":true,"request_id":"old","revision":0}')
            with patch('ground.urllib.request.build_opener') as build:
                build.return_value.open.return_value = fake
                with self.assertRaisesRegex(ValueError, '未确认'): link.send(batch)
        finally:
            server.shutdown(); server.server_close(); worker.join(2)


if __name__ == '__main__': unittest.main()
