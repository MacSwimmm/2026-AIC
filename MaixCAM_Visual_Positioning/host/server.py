#!/usr/bin/env python3
"""Local ControlDesk vision console. Start with python host/server.py."""
import argparse
import base64
import csv
import io
import json
import mimetypes
import os
import socket
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from device import Device
from geometry import Calibration, number

ROOT=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parent.parent))
WEB=ROOT/"host"/"web"


def atomic_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(".tmp")
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    os.replace(temp,path)


class Application:
    def __init__(self,data):
        self.data=Path(data);self.data.mkdir(parents=True,exist_ok=True)
        self.device=Device(ROOT,self.data)
        self.lock=threading.RLock();self.cal=None;self.targets=[];self.revision=0
        path=self.data/"calibration.json"
        if path.exists():
            try:self.cal=Calibration(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError,KeyError,TypeError):pass
        path=self.data/"targets.json"
        if path.exists():
            try:self.targets=json.loads(path.read_text(encoding="utf-8"))
            except (ValueError,TypeError):pass

    def compatible(self,meta):
        return bool(self.cal and self.cal.size==[meta.get("width"),meta.get("height")] and self.cal.device_id==meta.get("device_id") and self.cal.geometry_id==meta.get("geometry_id"))

    def ready(self):
        _,meta=self.device.snapshot()
        if not self.compatible(meta):raise ValueError("请先为当前相机和分辨率完成地面标定")
        return meta

    def save_cal(self):
        atomic_json(self.data/"calibration.json",self.cal.export())
        self.revision+=1

    def status(self):
        ds=self.device.status();meta=ds.get("metadata",{})
        with self.lock:
            compatible=self.compatible(meta) and ds["connected"]
            detections=[]
            for raw in meta.get("detections",[]) if ds["connected"] else []:
                obj=dict(raw)
                if compatible:
                    try:obj["world"]=self.cal.to_world(obj["anchor"])
                    except ValueError:obj["world"]=None
                detections.append(obj)
            return {"application":"maixcam-ground-console","version":"1.0.0","device":ds,"calibration":self.cal.export() if self.cal else None,"calibration_compatible":compatible,"revision":self.revision,"grid":self.cal.grid() if compatible else [],"targets":self.targets if compatible else [],"detections":detections}

    def post(self,path,body):
        with self.lock:
            if path=="/api/connect":
                self.device.connect(body.get("host",""),body.get("username","root"),body.get("password","root"),deploy=body.get("deploy",True))
                return {"ok":True}
            if path=="/api/disconnect":
                self.device.disconnect();return {"ok":True}
            if path=="/api/mode":
                self.device.snapshot();return self.device.set_mode(body["mode"])
            if path in ("/api/calibration","/api/calibration/import"):
                _,meta=self.device.snapshot()
                data=dict(body)
                if path.endswith("/import"):
                    if data.get("device_id")!=meta["device_id"] or data.get("geometry_id")!=meta["geometry_id"]:
                        raise ValueError("标定文件不属于当前相机或画面设置")
                    if data.get("image_size")!=[meta["width"],meta["height"]]:
                        raise ValueError("标定文件分辨率不匹配")
                else:
                    data.update(image_size=[meta["width"],meta["height"]],device_id=meta["device_id"],geometry_id=meta["geometry_id"],checks=[])
                cal=Calibration(data)
                self.cal=cal;self.targets=[];self.save_cal()
                atomic_json(self.data/"targets.json",[])
                return self.cal.export()
            if path=="/api/calibration/clear":
                self.cal=None;self.targets=[];self.revision+=1
                (self.data/"calibration.json").unlink(missing_ok=True)
                atomic_json(self.data/"targets.json",[])
                return {"ok":True}
            if path=="/api/check":
                self.ready();item=self.cal.check(body["pixel"],body["actual"]);self.save_cal();return item
            if path=="/api/point":
                self.ready()
                if "world" in body:
                    world=[number(x) for x in body["world"]]
                    if len(world)!=2:raise ValueError("请选择二维坐标")
                    pixel=self.cal.to_pixel(world)
                else:
                    pixel=[number(x) for x in body["pixel"]]
                    if len(pixel)!=2:raise ValueError("请选择二维坐标")
                if not (0<=pixel[0]<self.cal.size[0] and 0<=pixel[1]<self.cal.size[1]):
                    raise ValueError("点击位置不在画面内")
                point=self.cal.to_world(pixel)
                if not point["inside"]:raise ValueError("该点在标定区域外")
                return {**point,"pixel":pixel,"units":"m","assumption":"visible_ground_plane"}
            if path=="/api/targets":
                self.ready();pixel=[number(x) for x in body["pixel"]]
                point=self.cal.to_world(pixel)
                if not point["inside"]:raise ValueError("目标点必须位于标定区域内")
                item={"id":uuid.uuid4().hex[:8],"name":str(body.get("name") or f"目标 {len(self.targets)+1}")[:48],"x":point["x"],"y":point["y"],"pixel":pixel,"created_at":time.time(),"source":"manual_ground","units":"m"}
                self.targets.append(item);atomic_json(self.data/"targets.json",self.targets)
                return item
            if path=="/api/targets/clear":
                self.targets=[];atomic_json(self.data/"targets.json",[]);return {"ok":True}
            if path=="/api/targets/remove":
                self.targets=[x for x in self.targets if x["id"]!=body["id"]]
                atomic_json(self.data/"targets.json",self.targets);return {"ok":True}
        raise ValueError("未知操作")


class Handler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self,*args):pass

    @property
    def app(self):return self.server.app

    def reply(self,code,body,mime="application/json; charset=utf-8",download=None,headers=None):
        if isinstance(body,(dict,list)):body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
        elif isinstance(body,str):body=body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type",mime);self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        if download:self.send_header("Content-Disposition",f'attachment; filename="{download}"')
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass

    def do_GET(self):
        path=urlsplit(self.path).path
        try:
            if path=="/api/status":self.reply(200,self.app.status())
            elif path=="/api/frame.jpg":
                jpeg,meta=self.app.device.snapshot()
                with self.app.lock:
                    if self.app.compatible(meta):
                        meta["detections"]=[{**o,"world":self.app.cal.to_world(o["anchor"])} for o in meta.get("detections",[])]
                        meta["calibration_created_at"]=self.app.cal.created_at
                encoded=base64.b64encode(json.dumps(meta).encode()).decode()
                self.reply(200,jpeg,"image/jpeg",headers={"X-Frame-Meta":encoded})
            elif path=="/api/calibration/export":
                with self.app.lock:
                    if not self.app.cal:raise ValueError("还没有标定数据")
                    self.reply(200,self.app.cal.export(),download="ground-calibration.json")
            elif path=="/api/targets/export":
                with self.app.lock:
                    self.app.ready()
                    self.reply(200,{"frame":"ground","units":"m","navigation_sent":False,"device_id":self.app.cal.device_id,"calibration_created_at":self.app.cal.created_at,"targets":self.app.targets},download="ground-targets.json")
            elif path=="/api/targets.csv":
                with self.app.lock:
                    self.app.ready();s=io.StringIO();w=csv.writer(s);w.writerow(["id","name","x_m","y_m","source"])
                    for item in self.app.targets:
                        # Prevent spreadsheet formula execution when users name a target.
                        name=item["name"]
                        if name.startswith(("=","+","-","@")):name="'"+name
                        w.writerow([item["id"],name,item["x"],item["y"],item["source"]])
                    self.reply(200,"\ufeff"+s.getvalue(),"text/csv; charset=utf-8","ground-targets.csv")
            else:
                rel="index.html" if path=="/" else path.lstrip("/")
                p=(WEB/rel).resolve()
                if not p.is_relative_to(WEB.resolve()) or not p.is_file():self.reply(404,{"error":"页面不存在"});return
                self.reply(200,p.read_bytes(),mimetypes.guess_type(str(p))[0] or "application/octet-stream")
        except (ValueError,KeyError,TypeError) as e:self.reply(400,{"error":str(e)})

    def do_POST(self):
        # The web console is loopback-only and refuses cross-origin writes.
        origin=self.headers.get("Origin")
        if origin and urlsplit(origin).netloc!=self.headers.get("Host"):
            self.reply(403,{"error":"请求来源不匹配"});return
        try:
            n=int(self.headers.get("Content-Length",0))
            if not 0<n<=1_000_000:raise ValueError("请求内容大小不正确")
            if not self.headers.get("Content-Type","").startswith("application/json"):raise ValueError("需要 JSON 请求")
            body=json.loads(self.rfile.read(n))
            self.reply(200,self.app.post(urlsplit(self.path).path,body))
        except (ValueError,KeyError,TypeError) as e:self.reply(400,{"error":str(e)})
        except Exception as e:self.reply(502,{"error":str(e)})


class ConsoleServer(ThreadingHTTPServer):
    # Windows otherwise allows two SO_REUSEADDR listeners on the same port.
    allow_reuse_address=False
    def server_bind(self):
        if hasattr(socket,"SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


def main():
    p=argparse.ArgumentParser(description="MaixCAM 地面视觉定位上位机")
    p.add_argument("--port",type=int,default=29860);p.add_argument("--no-browser",action="store_true")
    default=Path(sys.executable).parent/"data" if getattr(sys,"frozen",False) else ROOT/"data"
    p.add_argument("--data-dir",type=Path,default=default)
    args=p.parse_args()
    try:
        server=ConsoleServer(("127.0.0.1",args.port),Handler)
    except OSError:
        import urllib.request
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{args.port}/api/status",timeout=2) as r:existing=json.load(r)
            if existing.get("application")=="maixcam-ground-console":
                if not args.no_browser:webbrowser.open(f"http://127.0.0.1:{args.port}")
                return
        except (OSError,ValueError):pass
        raise RuntimeError(f"端口 {args.port} 已被其他程序占用，请使用 --port 指定其他端口")
    server.daemon_threads=True
    server.app=Application(args.data_dir)
    print(f"MaixCAM 视觉定位：http://127.0.0.1:{args.port}",flush=True)
    if not args.no_browser:webbrowser.open(f"http://127.0.0.1:{args.port}")
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=="__main__":main()
