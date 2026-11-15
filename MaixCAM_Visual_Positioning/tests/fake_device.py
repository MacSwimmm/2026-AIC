"""A separate simulated device store for PC interface/browser tests."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'device'))
from ground import GroundStore
from device import Device


class MemoryDevice(Device):
    def __init__(self, data):
        super().__init__(ROOT, data)
        self.store = GroundStore(Path(data) / 'simulated-device')

    def device_meta(self):
        return {**self.meta, 'monotonic_at': time.monotonic() - (time.time() - self.meta.get('received_at', 0))}

    def ground_status(self): return self.store.status(self.device_meta())

    def ground_request(self, path, body=None):
        meta = self.device_meta()
        if body is not None: return self.store.post(path, body, meta)
        if path == '/api/targets/export': return self.store.export_targets(meta)
        if path == '/api/calibration/export':
            if not self.store.cal: raise ValueError('还没有标定数据')
            return self.store.cal.export()
        raise ValueError(path)

    def snapshot(self, fresh=True, after=None):
        jpeg, meta = super().snapshot(fresh, after=after)
        return jpeg, self.store.enrich(meta)
