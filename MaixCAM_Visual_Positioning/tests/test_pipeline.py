"""Frame pairing, bounded work, and multipart transport regression checks."""
import base64
import importlib.util
import io
import json
import sys
import threading
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from device import read_mjpeg

spec=importlib.util.spec_from_file_location('pipeline_agent',ROOT/'device'/'vision_agent.py')
agent=importlib.util.module_from_spec(spec);spec.loader.exec_module(agent)


class PipelineTests(unittest.TestCase):
    def test_upgrade_preserves_legacy_calibration_sensor_mode(self):
        from types import SimpleNamespace
        cfg={'fps':30,'performance_version':1}
        self.assertEqual(agent.capture_profile(cfg,SimpleNamespace(geometry_id='640x480-rgb-v1')),
                         (30,30,'640x480-rgb-v1'))
        self.assertEqual(agent.capture_profile(cfg),(30,30,'640x480-rgb-v1'))

    def test_new_sensor_geometry_cannot_silently_reuse_legacy_calibration(self):
        from types import SimpleNamespace
        cfg={'fps':30,'performance_version':1}
        profile=agent.capture_profile(cfg)
        self.assertEqual(profile,(60,45,'640x480-sensor60-v2'))
        # Once explicitly configured, a conflicting old calibration must be
        # rejected by GroundStore.compatible, not change the sensor implicitly.
        self.assertEqual(agent.capture_profile(cfg,SimpleNamespace(geometry_id='640x480-rgb-v1')),profile)

    def test_invalid_timing_fails_before_camera_allocation(self):
        for sensor,limit in [(80,45),(60,0),(30,45),(60,61)]:
            with self.subTest(sensor=sensor,limit=limit),self.assertRaises(ValueError):
                agent.capture_profile({'performance_version':2,'camera_fps':sensor,'fps':limit})

    def test_delayed_inference_retains_pixels_and_capture_timestamp_together(self):
        pairer=agent.InferenceFrames()
        first=bytearray(b'first camera RGB allocation')
        second=bytearray(b'second camera RGB allocation')
        self.assertIsNone(pairer.push(first,(1.,100.)))
        pixels,captured=pairer.push(second,(2.,101.))
        self.assertIs(pixels,first)
        self.assertEqual(captured,(1.,100.))
        self.assertEqual(pairer.push(bytearray(b'third'),(3.,102.)),(second,(2.,101.)))

    def test_transient_jpeg_miss_does_not_produce_invalid_bytes(self):
        from unittest.mock import Mock
        frame=Mock();frame.to_jpeg.return_value=None
        self.assertIsNone(agent.encode_image(frame,75))

    def test_multipart_keeps_each_jpeg_with_its_own_metadata(self):
        frames=[]
        for seq in (10,15):
            jpeg=b'\xff\xd8'+bytes([seq])+b'\xff\xd9'
            encoded=base64.b64encode(json.dumps({'seq':seq,'session_id':'camera-session'}).encode())
            frames.append(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 5\r\nX-Frame-Meta: '+encoded+b'\r\n\r\n'+jpeg+b'\r\n')
        stream=io.BytesIO(b''.join(frames))
        for seq in (10,15):
            pixels,meta=read_mjpeg(stream)
            self.assertEqual(pixels[2],seq);self.assertEqual(meta['seq'],seq)
        with self.assertRaises(OSError):read_mjpeg(stream)

    def test_truncated_and_oversized_multipart_frames_fail(self):
        for size in (10,4_000_001):
            stream=io.BytesIO(f'--frame\r\nContent-Length: {size}\r\nX-Frame-Meta: e30=\r\n\r\nshort'.encode())
            with self.assertRaises((OSError,ValueError)):read_mjpeg(stream)


if __name__=='__main__':unittest.main()
