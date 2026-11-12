"""A reachable HTTP agent with a frozen camera must not look like live video."""
import base64
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"host"))
from device import Device


class FrozenResponse:
    headers={"X-Frame-Meta":base64.b64encode(json.dumps({
        "seq":1,"device_id":"test-camera","mode":"off"
    }).encode()).decode()}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,*args):return b"\xff\xd8unchanging-jpeg-test-only"


class DeviceTests(unittest.TestCase):
    def test_repeated_http_jpeg_does_not_refresh_frame_age(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Device(Path(__file__).resolve().parents[1],temp)
            clock=[100.]
            stalled=threading.Event()
            def request(*args,**kwargs):
                clock[0]+=1
                if clock[0]>=106:stalled.set()
                return FrozenResponse()
            d.request=request
            stop=threading.Event()
            with patch("device.time.time",side_effect=lambda:clock[0]):
                worker=threading.Thread(target=d._poll,args=(stop,),daemon=True)
                worker.start()
                try:
                    self.assertTrue(stalled.wait(4),"polling did not reach frozen-frame condition")
                    self.assertFalse(d.status()["connected"])
                    with self.assertRaisesRegex(ValueError,"画面已中断"):
                        d.snapshot()
                    self.assertIn("帧序号停止更新",d.status()["message"])
                finally:
                    stop.set();worker.join(2)
                    self.assertFalse(worker.is_alive())


if __name__=="__main__":unittest.main()
