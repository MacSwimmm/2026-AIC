#!/usr/bin/env python3
"""MaixCAM camera owner. HTTP handlers never call camera/NN APIs.

The main thread owns the camera, detector and image buffers. All frame metadata
belongs to the JPEG from the same iteration. No navigation commands are sent.
"""
import argparse
import base64
import json
import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

lock=threading.Lock()
stop=threading.Event()
state={"ready":False,"error":"","mode":"off","requested_mode":"off"}
latest=None
TOKEN=""
restore_menu=True
STOCK_ROOT="/maixapp/apps/"
LAUNCHER=STOCK_ROOT+"launcher/launcher_daemon"


def process_state(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:return f.read().rsplit(") ",1)[1][0]
    except FileNotFoundError:return None


def stock_processes(daemon_only=False):
    found=[]
    for entry in os.listdir('/proc'):
        if not entry.isdigit():continue
        try:exe=os.readlink(f'/proc/{entry}/exe')
        except OSError:continue
        if exe.startswith(STOCK_ROOT) and (not daemon_only or exe==LAUNCHER):
            found.append(int(entry))
    return found


def stop_processes(pids):
    """Wait for resource owners to exit; never start another camera over them."""
    for pid in pids:
        if process_state(pid)=='D':
            raise RuntimeError("设备图像应用处于内核等待状态，请完整重启相机后连接")
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
    for sig,seconds in [(None,4),(signal.SIGKILL,2)]:
        if sig:
            for pid in pids:
                if process_state(pid) not in (None,'Z'):
                    try:os.kill(pid,sig)
                    except ProcessLookupError:pass
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if all(process_state(pid) in (None,'Z') for pid in pids):return
            time.sleep(.1)
    raise RuntimeError("设备旧应用尚未退出，已停止启动新取图程序，请重启相机后连接")


def prepare_device(folder):
    # Stop the supervisor before its child, including a configured startup app
    # such as Settings. Killing only 'launcher' leaves that app owning MMF.
    stop_processes(stock_processes(daemon_only=True))
    try:
        with open(os.path.join(folder,'agent.pid')) as f:pid=int(f.read().strip())
    except (FileNotFoundError,ValueError):pid=None
    if pid and process_state(pid) not in (None,'Z'):
        if process_state(pid)=='D':
            raise RuntimeError("旧取图程序处于内核等待状态，请完整重启相机后连接")
        try:
            with open(f'/proc/{pid}/cmdline','rb') as f:cmd=f.read()
        except FileNotFoundError:cmd=b''
        if os.path.join(folder,'vision_agent.py').encode() in cmd:stop_processes([pid])
    # A previous release may have restored the supervisor during termination.
    stop_processes(stock_processes(daemon_only=True))
    stop_processes(stock_processes())


def restore_after_exit(pid):
    while process_state(pid) not in (None,'Z'):time.sleep(.1)
    if not stock_processes(daemon_only=True):
        subprocess.Popen([LAUNCHER],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)


def handle_signal(*_):
    global restore_menu
    # Host takeover must not launch the stock UI alongside the next capture.
    restore_menu=False;stop.set()


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass

    def reply(self,code,data,content_type="application/json",headers=None):
        raw=json.dumps(data,ensure_ascii=False).encode() if content_type=="application/json" else data
        self.send_response(code)
        self.send_header("Content-Type",content_type)
        self.send_header("Content-Length",str(len(raw)))
        self.send_header("Cache-Control","no-store")
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass

    def authorized(self):
        import hmac
        if not hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+TOKEN):
            self.reply(401,{"error":"unauthorized"})
            return False
        return True

    def do_GET(self):
        if not self.authorized():return
        with lock:
            s=dict(state)
            frame=latest
        if self.path=="/status":self.reply(200,s)
        elif self.path=="/frame.jpg":
            if not frame:self.reply(503,{"error":"camera not ready"});return
            jpeg,meta=frame
            self.reply(200,jpeg,"image/jpeg",{"X-Frame-Meta":base64.b64encode(json.dumps(meta).encode()).decode()})
        else:self.reply(404,{"error":"not found"})

    def do_POST(self):
        if not self.authorized():return
        try:
            n=int(self.headers.get("Content-Length",0))
            if not 0<n<4096:raise ValueError("invalid body")
            body=json.loads(self.rfile.read(n))
            if self.path=="/mode":
                mode=body["mode"]
                if mode not in ("off","yolo11","red_blob","apriltag"):raise ValueError("invalid mode")
                with lock:state["requested_mode"]=mode
                self.reply(200,{"requested_mode":mode})
            elif self.path=="/stop":
                self.reply(200,{"stopping":True});stop.set()
            else:self.reply(404,{"error":"not found"})
        except (ValueError,KeyError) as e:self.reply(400,{"error":str(e)})


def main():
    global latest,TOKEN
    p=argparse.ArgumentParser();p.add_argument("--config")
    p.add_argument("--prepare",action="store_true");p.add_argument("--restore-after",type=int)
    args=p.parse_args()
    if args.prepare:
        prepare_device(os.path.dirname(os.path.abspath(__file__)));return
    if args.restore_after is not None:
        restore_after_exit(args.restore_after);return
    if not args.config:p.error('--config is required for capture')
    cfg=json.load(open(args.config));TOKEN=cfg["token"]
    server=ThreadingHTTPServer(("0.0.0.0",cfg.get("port",18765)),Handler)
    server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,handle_signal)
    cam=None
    try:
        from maix import camera,image,nn
        import faulthandler
        faulthandler.enable()
        # MaixPy installs its own handlers during import; set ours afterwards.
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,handle_signal)
        device_id=hashlib.sha256(open("/sys/class/cvi-base/base_uid","rb").read().strip()).hexdigest()[:16]
        width,height=cfg["width"],cfg["height"]
        active=cfg.get("initial_mode","off")
        if active not in ("off","yolo11","red_blob","apriltag"):
            raise ValueError("invalid initial mode")
        with lock:state["requested_mode"]=active
        detector=None
        if active=="yolo11":
            print("initializing YOLO before camera",flush=True)
            detector=nn.YOLO11(model="/root/models/yolo11n.mud",dual_buff=False)
        cam=camera.Camera(width,height,image.Format.FMT_RGB888)
        session_id=os.urandom(8).hex()
        seq=0;recent=deque(maxlen=30)
        while not stop.is_set():
            begin=time.monotonic()
            with lock:requested=state["requested_mode"]
            if requested!=active:
                print(f"detection mode: {active} -> {requested}",flush=True)
                # Camera.close() alone did not resolve mode-switch failures
                # on the tested firmware. Enter/leave NN mode in a fresh
                # process, allocating the model before any camera as in
                # Sipeed's example and releasing previous native allocations.
                if requested=="yolo11" or active=="yolo11":
                    cam.close();cam=None
                    detector=None
                    import gc;gc.collect()
                    cfg["initial_mode"]=requested
                    with open(args.config,"w") as f:json.dump(cfg,f)
                    server.shutdown();server.server_close()
                    os.execv(sys.executable,[sys.executable,"-u",os.path.abspath(__file__),"--config",args.config])
                active=requested
                recent.clear()
                print(f"detection mode ready: {active}",flush=True)
            frame=cam.read()
            if frame is None:continue
            detections=[]
            if active=="yolo11":
                # Stretching is explicit and reversed below; full-frame pixel
                # calibration never uses the detector's resized coordinates.
                small=frame.resize(detector.input_width(),detector.input_height(),image.Fit.FIT_FILL)
                for i,obj in enumerate(detector.detect(small,conf_th=.5,iou_th=.45)):
                    sx,sy=width/detector.input_width(),height/detector.input_height()
                    x,y,w,h=obj.x*sx,obj.y*sy,obj.w*sx,obj.h*sy
                    detections.append({"id":i,"label":detector.labels[obj.class_id],"score":obj.score,"bbox":[x,y,w,h],"anchor":[x+w/2,y+h],"anchor_kind":"bbox_bottom_approx"})
                del small
            elif active=="red_blob":
                for i,b in enumerate(frame.find_blobs([(15,85,20,90,5,85)],pixels_threshold=100,area_threshold=100,merge=True)):
                    detections.append({"id":i,"label":"red_marker","score":1.,"bbox":[b.x(),b.y(),b.w(),b.h()],"anchor":[b.cx(),b.cy()],"anchor_kind":"marker_center_assume_ground"})
            elif active=="apriltag":
                # This firmware's OpenMV fast framebuffer cannot hold a
                # 640x480 AprilTag working image. Keep the live geometry fixed.
                small=frame.resize(160,120)
                sx,sy=width/160,height/120
                for tag in small.find_apriltags():
                    detections.append({"id":tag.id(),"label":"tag_"+str(tag.id()),"score":1.,"bbox":[tag.x()*sx,tag.y()*sy,tag.w()*sx,tag.h()*sy],"anchor":[tag.cx()*sx,tag.cy()*sy],"anchor_kind":"tag_center_assume_ground"})
                del small
            jpeg=frame.to_jpeg(quality=75).to_bytes()
            del frame
            seq+=1;recent.append(time.monotonic())
            fps=(len(recent)-1)/(recent[-1]-recent[0]) if len(recent)>1 else 0
            meta={"seq":seq,"session_id":session_id,"captured_at":time.time(),"width":width,"height":height,"device_id":device_id,"geometry_id":"640x480-rgb-v1","mode":active,"detections":detections[:40],"fps":round(fps,1)}
            with lock:
                latest=(jpeg,meta);state.update(mode=active,ready=True,seq=seq,error="")
            stop.wait(max(0,1/cfg.get("fps",12)-(time.monotonic()-begin)))
    except Exception as e:
        with lock:state.update(ready=False,error=str(e))
        import traceback;traceback.print_exc()
        stop.wait(5)
    finally:
        if cam:
            try:cam.close()
            except Exception:pass
        server.shutdown()
        # The native library also releases resources at interpreter exit.
        # Wait for full process exit before starting the original startup UI.
        if restore_menu:
            subprocess.Popen([sys.executable,os.path.abspath(__file__),"--restore-after",str(os.getpid())],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)


if __name__=="__main__":main()
