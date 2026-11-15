"""Device-owned ground coordinates. One atomic document binds targets to calibration."""
import copy
import json
import os
import threading
import time
import uuid
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

from geometry import Calibration, number

VERSION = "2.1.0"
DATA_DIR = "/root/aic_vision/data"


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    # Persist the directory entry on Linux as well as the file contents.
    if os.name == "posix":
        fd = os.open(str(path.parent), os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


class GroundStore:
    def __init__(self, directory=DATA_DIR):
        self.path = Path(directory) / "ground.json"
        self.lock = threading.RLock()
        self.cal = None
        self.targets = []
        self.revision = 0
        self.load_error = ""
        self.grid = []
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("schema") != 2: raise ValueError("不支持的数据版本")
                self.cal = Calibration(data["calibration"]) if data["calibration"] else None
                self.targets = data["targets"]
                self.revision = int(data["revision"])
                if not isinstance(self.targets, list) or (self.targets and not self.cal):
                    raise ValueError("目标点缺少对应标定")
                for p in self.targets:
                    if not self.cal.to_world(p["pixel"])["inside"]:
                        raise ValueError("目标点与标定不匹配")
                    if any(abs(number(p[k]) - self.cal.to_world(p["pixel"])[k]) > 1e-6 for k in ("x", "y")):
                        raise ValueError("目标点坐标与标定不匹配")
                self.grid = self.cal.grid() if self.cal else []
            except (OSError, ValueError, KeyError, TypeError) as e:
                # Retain the damaged file for recovery; never silently overwrite it.
                self.cal = None; self.targets = []; self.load_error = str(e)

    def compatible(self, meta):
        return bool(self.cal and self.cal.size == [meta.get("width"), meta.get("height")]
                    and self.cal.device_id == meta.get("device_id")
                    and self.cal.geometry_id == meta.get("geometry_id"))

    def require(self, meta):
        if not meta or time.monotonic() - meta.get("monotonic_at", 0) > 3:
            raise ValueError("相机画面已中断，请等待新画面")
        if not self.compatible(meta): raise ValueError("请先为当前相机完成地面标定")

    def status(self, meta):
        with self.lock:
            compatible = self.compatible(meta)
            return copy.deepcopy({"calibration": self.cal.export() if self.cal else None,
                "calibration_compatible": compatible, "targets": self.targets if compatible else [],
                "grid": self.grid if compatible else [], "revision": self.revision,
                "storage": "maixcam", "load_error": self.load_error})

    def commit(self, cal, targets):
        revision = self.revision + 1
        atomic_json(self.path, {"schema": 2, "revision": revision,
            "calibration": cal.export() if cal else None, "targets": targets})
        self.cal, self.targets, self.revision = cal, targets, revision
        self.grid = cal.grid() if cal else []
        self.load_error = ""

    def point(self, body):
        if "world" in body:
            world = [number(x) for x in body["world"]]
            if len(world) != 2: raise ValueError("请选择二维坐标")
            pixel = self.cal.to_pixel(world)
        else:
            pixel = [number(x) for x in body["pixel"]]
        if len(pixel) != 2: raise ValueError("请选择二维坐标")
        if not (0 <= pixel[0] < self.cal.size[0] and 0 <= pixel[1] < self.cal.size[1]):
            raise ValueError("点击位置不在画面内")
        point = self.cal.to_world(pixel)
        if not point["inside"]: raise ValueError("该点在标定区域外")
        return {**point, "pixel": pixel, "units": "m", "assumption": "visible_ground_plane"}

    def post(self, path, body, meta):
        if not isinstance(body, dict): raise ValueError("需要 JSON 对象")
        with self.lock:
            if path in ("/api/calibration", "/api/calibration/import"):
                if not meta or time.monotonic() - meta.get("monotonic_at", 0) > 3:
                    raise ValueError("请等待实时画面")
                data = dict(body)
                if path.endswith("/import"):
                    if any(data.get(k) != meta.get(k) for k in ("device_id", "geometry_id")):
                        raise ValueError("标定文件不属于当前相机或画面设置")
                    if data.get("image_size") != [meta["width"], meta["height"]]:
                        raise ValueError("标定文件分辨率不匹配")
                else:
                    data.update(image_size=[meta["width"], meta["height"]], device_id=meta["device_id"],
                                geometry_id=meta["geometry_id"], checks=[])
                cal = Calibration(data)
                self.commit(cal, [])
                return cal.export()
            if path == "/api/calibration/clear":
                self.commit(None, []); return {"ok": True}
            self.require(meta)
            if path == "/api/point": return self.point(body)
            if path == "/api/check":
                cal = Calibration(self.cal.export())
                result = cal.check(body["pixel"], body["actual"])
                self.commit(cal, self.targets); return result
            if path == "/api/targets":
                point = self.point(body)
                item = {"id": uuid.uuid4().hex[:8], "name": str(body.get("name") or f"目标 {len(self.targets)+1}")[:48],
                    "x": point["x"], "y": point["y"], "pixel": point["pixel"], "created_at": time.time(),
                    "source": "manual_ground", "units": "m"}
                self.commit(self.cal, [*self.targets, item]); return item
            if path == "/api/targets/clear":
                self.commit(self.cal, []); return {"ok": True}
            if path == "/api/targets/remove":
                self.commit(self.cal, [p for p in self.targets if p["id"] != body["id"]]); return {"ok": True}
        raise ValueError("未知操作")

    def export_targets(self, meta):
        with self.lock:
            self.require(meta)
            return copy.deepcopy({"schema": 2, "frame": "ground", "units": "m", "navigation_sent": False,
                "device_id": self.cal.device_id, "calibration_created_at": self.cal.created_at,
                "revision": self.revision, "targets": self.targets})

    def enrich(self, meta):
        with self.lock:
            meta = dict(meta)
            if self.compatible(meta):
                detections = []
                for raw in meta.get("detections", []):
                    obj = dict(raw)
                    try: obj["world"] = self.cal.to_world(obj["anchor"])
                    except ValueError: obj["world"] = None
                    detections.append(obj)
                meta.update(detections=detections, calibration_created_at=self.cal.created_at,
                            ground_revision=self.revision)
            return meta


class PiLink:
    """Explicit target batch delivery. A transport reply alone is not acceptance."""
    def __init__(self, directory=DATA_DIR):
        self.path = Path(directory) / 'pi.json'
        self.lock = threading.Lock()
        self.url = ''; self.message = '尚未配置树莓派接收地址'
        if self.path.exists():
            try: self.url = json.loads(self.path.read_text(encoding='utf-8'))['url']
            except (ValueError, KeyError, OSError): pass
        if self.url:self.message='树莓派接收地址已载入'

    def status(self):
        with self.lock: return {'url': self.url, 'message': self.message}

    def configure(self, body):
        url = str(body.get('url', '')).strip()
        parts = urlsplit(url)
        if url and (parts.scheme != 'http' or not parts.hostname or parts.username or parts.password or parts.fragment):
            raise ValueError('请输入树莓派 HTTP 接收地址，例如 http://192.168.1.20:18766/targets')
        with self.lock:
            atomic_json(self.path, {'url': url})
            self.url = url; self.message = '接收地址已保存' if url else '尚未配置树莓派接收地址'
        return self.status()

    def send(self, batch):
        with self.lock: url = self.url
        if not url: raise ValueError('请先在电脑上位机的目标点页填写树莓派接收地址')
        if not batch['targets']: raise ValueError('请先保存目标点')
        delivery = {**batch, 'request_id': uuid.uuid4().hex, 'type': 'ground_targets'}
        request = urllib.request.Request(url, data=json.dumps(delivery, ensure_ascii=False, allow_nan=False).encode(),
                                         headers={'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=4) as response: ack = json.loads(response.read(65536))
            if not isinstance(ack,dict) or ack.get('accepted') is not True or ack.get('request_id') != delivery['request_id'] or ack.get('revision') != delivery['revision']:
                raise ValueError('树莓派未确认本批目标点')
        except (OSError, ValueError) as e:
            with self.lock: self.message = '发送未确认：' + str(e)
            raise ValueError(self.message) from e
        with self.lock: self.message = f"树莓派已确认接收 {len(batch['targets'])} 个目标点"
        return {'accepted': True, 'request_id': delivery['request_id'], 'revision': delivery['revision'],
                'message': self.message, 'vehicle_motion_verified': False}
