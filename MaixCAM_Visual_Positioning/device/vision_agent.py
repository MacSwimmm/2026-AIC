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
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

lock=threading.Lock()
frame_changed=threading.Condition(lock)
screen_requested=threading.Event()
jpeg_until=0
stream_viewers=0
screen_version=0
stop=threading.Event()
state={"ready":False,"error":"","mode":"off","requested_mode":"off"}
latest=None
capture_meta={}
screen_snapshot=None
ground=None
pi_link=None
pixels=None
TOKEN=""
restore_menu=True
STOCK_ROOT="/maixapp/apps/"
LAUNCHER=STOCK_ROOT+"launcher/launcher_daemon"


class InferenceFrames:
    """Retain the exact RGB image for YOLO's one-input delayed result."""
    def __init__(self):self.pending=None

    def push(self, frame, captured):
        previous=self.pending
        self.pending=(frame,captured)
        return previous


def capture_profile(cfg, calibration=None):
    """Keep an existing 30 FPS calibration on its original sensor timing."""
    if cfg.get("performance_version",0)<2:
        legacy=bool(calibration and calibration.geometry_id=="640x480-rgb-v1")
        cfg.update(camera_fps=30 if legacy else 60,fps=30 if legacy else 45,performance_version=2)
    sensor=int(cfg.get("camera_fps",60));limit=float(cfg.get("fps",45))
    if sensor not in (30,60) or not 0<limit<=sensor:
        raise ValueError("camera_fps must be 30 or 60; fps must be positive and <= camera_fps")
    geometry="640x480-rgb-v1" if sensor==30 else "640x480-sensor60-v2"
    return sensor,limit,geometry


def encode_image(frame, quality):
    if pixels is not None:
        from maix import image
        if frame.format()==image.Format.FMT_RGB888:frame=pixels.nv21(frame)
    compressed=frame.to_jpeg(quality=quality)
    return compressed.to_bytes() if compressed is not None else None


encoded_recent=deque(maxlen=60)


def publish_jpeg(jpeg, meta, screen=False):
    global latest,screen_snapshot,screen_version
    with frame_changed:
        if screen:
            screen_snapshot=jpeg;screen_version+=1
        else:
            latest=(jpeg,meta);encoded_recent.append(time.monotonic())
            fps=(len(encoded_recent)-1)/(encoded_recent[-1]-encoded_recent[0]) if len(encoded_recent)>1 else 0
            state.update(stream_fps=round(fps,1),encoder_error="")
        frame_changed.notify_all()


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
        # D is normal while camera/NN code waits for the driver. Judge failure
        # by failure to exit after a bounded stop, never by one sampled state.
        try:
            with open(f'/proc/{pid}/cmdline','rb') as f:cmd=f.read()
        except FileNotFoundError:cmd=b''
        if b'vision_agent.py' in cmd and (folder.encode() in cmd or b'/maixapp/apps/aic_ground/' in cmd):
            stop_processes([pid])
    # A previous release may have restored the supervisor during termination.
    stop_processes(stock_processes(daemon_only=True))
    stop_processes(stock_processes())


def restore_after_exit(pid):
    while process_state(pid) not in (None,'Z'):time.sleep(.1)
    if not stock_processes(daemon_only=True):
        env={k:v for k,v in os.environ.items() if k!='AIC_OWNER_FD'}
        subprocess.Popen([LAUNCHER],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,env=env)


def handle_signal(*_):
    global restore_menu
    # Host takeover must not launch the stock UI alongside the next capture.
    restore_menu=False;stop.set()


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass

    def reply(self,code,data,content_type="application/json",headers=None):
        raw=b'' if code==304 else json.dumps(data,ensure_ascii=False).encode() if content_type=="application/json" else data
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
        global jpeg_until,stream_viewers
        if not self.authorized():return
        path=urlsplit(self.path).path
        if path=="/stream.mjpg":
            self.connection.settimeout(3)
            self.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            self.connection.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,65536)
            with frame_changed:stream_viewers+=1
            previous=None
            try:
                self.send_response(200)
                self.send_header("Content-Type","multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control","no-store")
                self.end_headers()
                while not stop.is_set():
                    with frame_changed:
                        jpeg_until=time.monotonic()+2
                        frame_changed.wait_for(lambda:stop.is_set() or latest and latest[0] and
                            time.monotonic()-latest[1]["monotonic_at"]<1 and
                            (latest[1]["session_id"],latest[1]["seq"])!=previous,timeout=1)
                        frame=latest
                    if stop.is_set():break
                    if not frame or not frame[0]:continue
                    jpeg,meta=frame
                    if time.monotonic()-meta["monotonic_at"]>=1:continue
                    identity=(meta["session_id"],meta["seq"])
                    if identity==previous:continue
                    previous=identity
                    wire_meta={**meta,"agent_frame_age_ms":round((time.monotonic()-meta["monotonic_at"])*1000,2)}
                    encoded=base64.b64encode(json.dumps(wire_meta).encode()).decode()
                    header=f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\nX-Frame-Meta: {encoded}\r\n\r\n".encode()
                    self.wfile.write(header+jpeg+b"\r\n");self.wfile.flush()
            except OSError:pass
            finally:
                with frame_changed:stream_viewers-=1
            return
        if path=="/frame.jpg":
            after=parse_qs(urlsplit(self.path).query).get("after",[""])[0]
            with frame_changed:
                jpeg_until=time.monotonic()+2
                frame_changed.wait_for(lambda: latest and latest[0] and
                    time.monotonic()-latest[1]["monotonic_at"]<1 and
                    f"{latest[1]['session_id']}:{latest[1]['seq']}"!=after,timeout=1)
                frame=latest
            if not frame or not frame[0] or time.monotonic()-frame[1]["monotonic_at"]>=1:
                self.reply(503,{"error":"camera not ready"});return
            jpeg,meta=frame
            wire_meta={**meta,"agent_frame_age_ms":round((time.monotonic()-meta["monotonic_at"])*1000,2)}
            self.reply(200,jpeg,"image/jpeg",{"X-Frame-Meta":base64.b64encode(json.dumps(wire_meta).encode()).decode()})
            return
        if path=="/screen.jpg":
            with frame_changed:
                before=screen_version
                screen_requested.set()
                frame_changed.wait_for(lambda:screen_version>before,timeout=2)
                screen=screen_snapshot
            if screen:self.reply(200,screen,"image/jpeg")
            else:self.reply(503,{"error":"屏幕正在初始化"})
            return
        with lock:
            s=dict(state)
            frame=latest
        with lock:meta=capture_meta
        if self.path=="/status":self.reply(200,{**s,"version":"2.2.0","storage":"maixcam","transport":"mjpeg"})
        elif self.path in ("/ground/status","/api/status"):
            pi=pi_link.status()
            with ground.lock:
                # Frame sequence changes do not change the saved grid. Avoid
                # copying/serializing it every half second while streaming.
                identity=[ground.revision,ground.compatible(meta),ground.load_error,pi]
                etag='"'+hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()+'"'
                unchanged=self.headers.get('If-None-Match')==etag
                payload=None if unchanged else {**ground.status(meta),'pi':pi}
            self.reply(304,b'',headers={'ETag':etag}) if unchanged else self.reply(200,payload,headers={'ETag':etag})
        elif self.path=="/api/targets/export":
            try:self.reply(200,ground.export_targets(meta))
            except ValueError as e:self.reply(400,{"error":str(e)})
        elif self.path=="/api/calibration/export":
            cal=ground.status(meta)["calibration"]
            self.reply(200,cal) if cal else self.reply(400,{"error":"还没有标定数据"})
        else:self.reply(404,{"error":"not found"})

    def do_POST(self):
        if not self.authorized():return
        try:
            n=int(self.headers.get("Content-Length",0))
            if not 0<n<=1_000_000:raise ValueError("invalid body")
            body=json.loads(self.rfile.read(n))
            if not isinstance(body,dict):raise ValueError("需要 JSON 对象")
            if self.path=="/mode":
                mode=body["mode"]
                if mode not in ("off","yolo11","red_blob","apriltag"):raise ValueError("invalid mode")
                with lock:state["requested_mode"]=mode
                self.reply(200,{"requested_mode":mode})
            elif self.path=="/stop":
                self.reply(200,{"stopping":True});stop.set()
            elif self.path=='/api/pi/config':self.reply(200,pi_link.configure(body))
            elif self.path=='/api/pi/send':
                with lock:meta=capture_meta
                self.reply(200,pi_link.send(ground.export_targets(meta)))
            elif self.path.startswith('/api/'):
                with lock:meta=capture_meta
                self.reply(200,ground.post(self.path,body,meta))
            else:self.reply(404,{"error":"not found"})
        except (ValueError,KeyError,TypeError) as e:self.reply(400,{"error":str(e)})
        except OSError as e:self.reply(500,{"error":"设备保存失败："+str(e)})


def main():
    global latest,TOKEN,ground,screen_snapshot,pi_link,screen_version,capture_meta,pixels
    p=argparse.ArgumentParser();p.add_argument("--config")
    p.add_argument("--prepare",action="store_true");p.add_argument("--restore-after",type=int)
    args=p.parse_args()
    if args.prepare:
        prepare_device(os.path.dirname(os.path.abspath(__file__)));return
    if args.restore_after is not None:
        restore_after_exit(args.restore_after);return
    if not args.config:p.error('--config is required for capture')
    cfg=json.load(open(args.config));TOKEN=cfg["token"]
    # The launcher and PC deployment share one data directory and one camera owner.
    import fcntl
    inherited=os.environ.get('AIC_OWNER_FD')
    if inherited:
        owner=os.fdopen(int(inherited),'w')
    else:
        owner=open(os.path.join(os.path.dirname(args.config),'owner.lock'),'w')
        try:fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('地面定位已经运行，请使用现有应用')
    with open(os.path.join(os.path.dirname(args.config),'agent.pid'),'w') as f:f.write(str(os.getpid()))
    # Keep the lock across execv during YOLO mode changes.
    os.set_inheritable(owner.fileno(),True)
    os.environ['AIC_OWNER_FD']=str(owner.fileno())
    from ground import GroundStore,PiLink,atomic_json
    ground=GroundStore();pi_link=PiLink()
    camera_fps,fps_limit,geometry_id=capture_profile(cfg,ground.cal)
    atomic_json(args.config,cfg)
    server=ThreadingHTTPServer(("0.0.0.0",cfg.get("port",18765)),Handler)
    server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,handle_signal)
    cam=None;ui=None
    try:
        from maix import camera,image,nn,app
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
            detector=nn.YOLO11(model="/root/models/yolo11n.mud",dual_buff=True)
        # One VI channel only. The tested driver could hang on restart after
        # allocating a second channel alongside JPEG/display and YOLO.
        # Ordinary video stays NV21 through JPEG; RGB conversion is only for
        # local drawing. Recognition always uses the original RGB camera path.
        if active=="off" and camera_fps==60:
            try:
                from pixel_convert import Pixels
                pixels=Pixels()
            except (ImportError,OSError,AttributeError) as e:
                print("NV21 preview unavailable; using RGB:",type(e).__name__,flush=True)
        cam=camera.Camera(width,height,image.Format.FMT_YVU420SP if pixels else image.Format.FMT_RGB888,fps=camera_fps)
        if cfg.get('display',True):
            from native_ui import Screen
            def set_mode(mode):
                with lock:state['requested_mode']=mode
            ui=Screen(ground,set_mode,stop.set,pi_link)
        session_id=os.urandom(8).hex()
        seq=0;recent=deque(maxlen=60);display_recent=deque(maxlen=30);next_display=0;display_seq=0
        next_tick=time.monotonic();inference_frames=InferenceFrames()
        while not stop.is_set() and not app.need_exit():
            begin=time.monotonic()
            with lock:requested=state["requested_mode"]
            if requested!=active:
                print(f"detection mode: {active} -> {requested}",flush=True)
                # Camera.close() alone did not resolve mode-switch failures
                # on the tested firmware. Enter/leave NN mode in a fresh
                # process, allocating the model before any camera as in
                # Sipeed's example and releasing previous native allocations.
                if requested in ("off","yolo11") or active in ("off","yolo11"):
                    if ui:ui.close();ui=None
                    cam.close();cam=None
                    detector=None
                    import gc;gc.collect()
                    cfg["initial_mode"]=requested
                    from ground import atomic_json
                    atomic_json(args.config,cfg)
                    server.shutdown();server.server_close()
                    os.execv(sys.executable,[sys.executable,"-u",os.path.abspath(__file__),"--config",args.config])
                active=requested
                recent.clear()
                print(f"detection mode ready: {active}",flush=True)
            frame=cam.read()
            if frame is None:continue
            captured=time.monotonic();frame_captured=(captured,time.time())
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
                previous=inference_frames.push(frame,frame_captured)
                if previous is None:continue
                frame,frame_captured=previous
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
            detected=time.monotonic()
            seq+=1;recent.append(detected)
            fps=(len(recent)-1)/(recent[-1]-recent[0]) if len(recent)>1 else 0
            meta={"seq":seq,"session_id":session_id,"captured_at":frame_captured[1],"monotonic_at":frame_captured[0],"width":width,"height":height,"device_id":device_id,"geometry_id":geometry_id,"mode":active,"detections":detections[:40],"fps":round(fps,1),"camera_fps":camera_fps,"fps_limit":fps_limit,"pixel_format":"nv21" if pixels else "rgb888"}
            meta=ground.enrich(meta)
            # Standalone operation needs no network JPEG. HTTP readers lease
            # encoding for two seconds; every published JPEG retains its own
            # capture's detections. Native and NN calls stay on this thread.
            # Keep native channel allocation in the same order as the original
            # always-encoding app: JPEG before the first display.show().
            # A transient encoder miss only drops a network frame.
            jpeg=encode_image(frame,75) if seq==1 or time.monotonic()<jpeg_until else None
            encoded=time.monotonic()
            capture_screen=screen_requested.is_set()
            if capture_screen:screen_requested.clear()
            if ui:
                with ground.lock:
                    ui.set_remote_view(stream_viewers>0)
                    ui.poll(meta)
                    # A connected PC owns the preview. Draw only controls on
                    # native UI changes; keep polling touches on every frame.
                    display_fps=8 if active!="off" else 30
                    if (not ui.remote_view and time.monotonic()>=next_display) or capture_screen or ui.dirty:
                        ui.draw(pixels.rgb(frame) if pixels and not ui.remote_view else frame,meta,capture_screen=capture_screen)
                        display_recent.append(time.monotonic());display_seq+=1
                        next_display=time.monotonic()+1/display_fps
            drawn=time.monotonic()
            while display_recent and drawn-display_recent[0]>2:display_recent.popleft()
            meta["timings_ms"]={"capture":round((captured-begin)*1000,2),
                "detect":round((detected-captured)*1000,2),"jpeg":round((encoded-detected)*1000,2),
                "display":round((drawn-encoded)*1000,2)}
            with frame_changed:
                capture_meta=meta
                screen_fps=(len(display_recent)-1)/(display_recent[-1]-display_recent[0]) if len(display_recent)>1 else 0
                state.update(mode=active,ready=True,seq=seq,fps=meta["fps"],display_seq=display_seq,
                             camera_fps=camera_fps,fps_limit=fps_limit,geometry_id=geometry_id,pixel_format=meta["pixel_format"],
                             pc_viewers=stream_viewers,local_preview_enabled=bool(ui and not ui.remote_view),
                             local_preview_seq=ui.preview_seq if ui else 0,screen_page=ui.page if ui else None,
                             screen_fps=round(screen_fps,1),detection_count=len(detections),
                             inference_seq=seq if active=="yolo11" else None,timings_ms=meta["timings_ms"],error="")
                frame_changed.notify_all()
            if jpeg:publish_jpeg(jpeg,meta)
            if ui and capture_screen:
                screen=encode_image(ui.screen_frame,75)
                if screen:publish_jpeg(screen,meta,screen=True)
            del frame
            period=1/fps_limit
            next_tick=max(next_tick+period,time.monotonic()-period)
            stop.wait(max(0,next_tick-time.monotonic()))
    except Exception as e:
        with lock:state.update(ready=False,error=str(e))
        import traceback;traceback.print_exc()
        stop.wait(5)
    finally:
        if ui:ui.close()
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
