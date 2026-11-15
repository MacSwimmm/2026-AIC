#!/usr/bin/env python3
"""Local ControlDesk vision console. Start with python host/server.py."""
import argparse
import base64
import csv
import io
import json
import mimetypes
import socket
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from device import Device

ROOT=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parent.parent))
WEB=ROOT/"host"/"web"



class Application:
    """The PC is a viewer/editor; MaixCAM is the only coordinate/data authority."""
    def __init__(self, data):
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.device = Device(ROOT, self.data)

    def status(self):
        ds = self.device.status()
        ground = self.device.ground_status()
        online = ds["connected"]
        return {"application": "maixcam-ground-console", "version": "2.0.0", "device": ds,
            **ground, "calibration_compatible": bool(online and ground.get("calibration_compatible")),
            "targets": ground.get("targets", []) if online else [],
            "detections": ds.get("metadata", {}).get("detections", []) if online else []}

    def post(self, path, body):
        if path == "/api/connect":
            self.device.connect(body.get("host", ""), body.get("username", "root"), body.get("password", "root"),
                                deploy=body.get("deploy", True))
            return {"ok": True}
        if path == "/api/disconnect":
            self.device.disconnect(); return {"ok": True}
        if path == "/api/mode":
            self.device.snapshot(); return self.device.set_mode(body["mode"])
        self.device.snapshot()
        return self.device.ground_request(path, body)

    def export(self, path):
        self.device.snapshot()
        return self.device.ground_request(path)


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
                encoded=base64.b64encode(json.dumps(meta).encode()).decode()
                self.reply(200,jpeg,"image/jpeg",headers={"X-Frame-Meta":encoded})
            elif path=="/api/calibration/export":
                self.reply(200,self.app.export(path),download="ground-calibration.json")
            elif path=="/api/targets/export":
                self.reply(200,self.app.export(path),download="ground-targets.json")
            elif path=="/api/targets.csv":
                exported=self.app.export("/api/targets/export")
                s=io.StringIO();w=csv.writer(s);w.writerow(["id","name","x_m","y_m","source"])
                for item in exported["targets"]:
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
        except Exception as e:self.reply(502,{"error":str(e)})

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
