"""SSH deployment and authenticated JPEG transport for the MaixCAM agent."""
import base64
import hashlib
import json
import ipaddress
import secrets
import shlex
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import paramiko

REMOTE = "/root/aic_vision"
AGENT_PORT = 18765
USB_HOSTS = ("10.191.17.1", "10.191.18.1")


def ssh_socket(host,lookup_timeout=2):
    """Bound DNS separately: socket connection timeouts do not cover getaddrinfo."""
    try:
        ipaddress.ip_address(host.split("%",1)[0])
        addresses=socket.getaddrinfo(host,22,type=socket.SOCK_STREAM,flags=socket.AI_NUMERICHOST)
    except ValueError:
        done=threading.Event();result=[];errors=[]
        def lookup():
            try:result.extend(socket.getaddrinfo(host,22,type=socket.SOCK_STREAM))
            except OSError as e:errors.append(e)
            finally:done.set()
        threading.Thread(target=lookup,daemon=True).start()
        if not done.wait(lookup_timeout):
            raise TimeoutError("相机名称解析超时")
        if errors:raise errors[0]
        addresses=result
    # USB offers IPv4 directly. Prefer it when a name also resolves to
    # interface-scoped IPv6 addresses; retain IPv6 as a supported fallback.
    addresses.sort(key=lambda entry:entry[0]!=socket.AF_INET)
    deadline=time.monotonic()+5;last=None
    for family,kind,proto,_,address in addresses:
        remaining=deadline-time.monotonic()
        if remaining<=0:break
        sock=socket.socket(family,kind,proto)
        try:
            sock.settimeout(min(2,remaining));sock.connect(address);return sock
        except OSError as e:last=e;sock.close()
    raise last or OSError("无法建立相机连接")


def ssh_connect(host, username, password, known_hosts):
    # Persist the first key for this directly connected device; reject later changes.
    c = paramiko.SSHClient()
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.touch(exist_ok=True)
    c.load_host_keys(str(known_hosts))
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock=None
    try:
        sock=ssh_socket(host)
        c.connect(host, username=username, password=password, timeout=5,sock=sock,
                  banner_timeout=6, auth_timeout=6, look_for_keys=False, allow_agent=False)
    except Exception:
        if sock:sock.close()
        c.close();raise
    return c


def remote(c, command, timeout=12):
    _, out, err = c.exec_command(command, timeout=timeout)
    stdout = out.read().decode("utf-8", "replace")
    stderr = err.read().decode("utf-8", "replace")
    status = out.channel.recv_exit_status()
    if status:
        raise RuntimeError((stderr or stdout or f"设备命令失败 ({status})")[-1800:])
    return stdout


class Device:
    def __init__(self, root, data):
        self.root, self.data = Path(root), Path(data)
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.frame = None
        self.meta = {}
        self.state = {"connected": False, "busy": False, "message": "连接相机后开始取图", "host": "", "mode": "off"}
        self.host = ""
        self.token = ""
        self.stop = threading.Event()
        self.worker = None
        self.config_path = self.data / "connection.json"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if self.config_path.exists():
            try:
                cfg=json.loads(self.config_path.read_text(encoding="utf-8"))
                self.host,self.token=cfg["host"],cfg["token"]
                self.state["host"]=self.host
            except (ValueError,KeyError):
                pass

    def status(self):
        with self.lock:
            s={**self.state,"metadata":dict(self.meta)}
            age=time.time()-self.meta.get("received_at",0)
            s["frame_age_s"]=age if self.meta else None
            s["connected"]=bool(s["connected"] and age<3)
            s["reconnecting"]=bool(not s["connected"] and not s["busy"] and
                self.worker and self.worker.is_alive() and not self.stop.is_set())
            if s["reconnecting"]:
                s["message"]="画面暂时中断，正在自动恢复连接…"
            return s

    def snapshot(self, fresh=True):
        with self.lock:
            if not self.frame or (fresh and time.time()-self.meta.get("received_at",0)>3):
                raise ValueError("相机画面已中断，请重新连接")
            return self.frame,dict(self.meta)

    def request(self, path, body=None, timeout=5, host=None):
        data=None if body is None else json.dumps(body).encode()
        address=self.host if host is None else host
        # urllib unquotes URL authorities. Escape the IPv6 zone delimiter so
        # e.g. %12 remains an interface index rather than a control character.
        authority=f"[{address.replace('%', '%25')}]" if ":" in address else address
        req=urllib.request.Request(f"http://{authority}:{AGENT_PORT}{path}",data=data,
            headers={"Authorization":"Bearer "+self.token,"Content-Type":"application/json"})
        return self.opener.open(req,timeout=timeout)

    def connect(self, host="", username="root", password="root", deploy=True):
        if not self.operation.acquire(blocking=False):
            raise ValueError("正在连接设备，请稍候")
        with self.lock:
            self.state.update(busy=True,message="正在建立设备连接…",connected=False)
            self.frame=None;self.meta={}
        threading.Thread(target=self._connect,args=(host.strip(),username,password,deploy),daemon=True).start()

    def _connect(self, host, username, password, deploy):
        c=None
        try:
            self.stop.set()
            if self.worker:
                self.worker.join(6)
            local_name=host[:-6] if host.lower().endswith('.local') else None
            candidates=([host,*USB_HOSTS] if local_name else [host]) if host else list(dict.fromkeys([*USB_HOSTS,self.host]))
            failures=[]
            for address in filter(None,candidates):
                try:
                    c=ssh_connect(address,username,password,self.data/"known_hosts")
                    # A .local name may resolve in SSH yet stall on every
                    # subsequent HTTP request. Pin the peer we authenticated;
                    # do not resolve a device name for each video frame.
                    peer_info=c.get_transport().getpeername()
                    peer=peer_info[0]
                    if len(peer_info)>3 and peer_info[3] and '%' not in peer:
                        peer+='%' + str(peer_info[3])
                    ipaddress.ip_address(peer.split("%",1)[0])
                    self.host=peer
                    break
                except (OSError,paramiko.SSHException) as e:
                    failures.append(f"{address}: {type(e).__name__}")
            if c is None:
                raise RuntimeError("未连接到相机。检查数据线或填写设备地址。"+"；".join(failures))
            model=remote(c,"cat /boot/board; cat /etc/hostname")
            if "maixcam" not in model.lower():
                raise RuntimeError("该地址未识别为 MaixCAM，已停止部署")
            if local_name and model.splitlines()[-1].strip().lower()!=local_name.lower():
                raise RuntimeError("USB 相机名称与指定名称不同，请使用自动查找或填写实际地址")
            if deploy:
                self.token=secrets.token_urlsafe(32)
                remote(c,f"mkdir -p {REMOTE}; chmod 700 {REMOTE}")
                sftp=c.open_sftp()
                try:
                    sftp.put(str(self.root/"device"/"vision_agent.py"),REMOTE+"/vision_agent.py")
                    with sftp.open(REMOTE+"/agent_config.json","w") as f:
                        f.write(json.dumps({"token":self.token,"port":AGENT_PORT,"width":640,"height":480,"fps":12}))
                    sftp.chmod(REMOTE+"/agent_config.json",0o600)
                finally:
                    sftp.close()
                remote(c,f"/usr/bin/python3 {REMOTE}/vision_agent.py --prepare",timeout=25)
                remote(c,f"nohup /usr/bin/python3 -u {REMOTE}/vision_agent.py --config {REMOTE}/agent_config.json >{REMOTE}/agent.log 2>&1 </dev/null & echo $! >{REMOTE}/agent.pid")
            deadline=time.monotonic()+40
            last="设备正在启动相机"
            while time.monotonic()<deadline:
                try:
                    with self.request("/status",timeout=2) as response:
                        status=json.load(response)
                    if status.get("ready"):
                        break
                    last=status.get("error") or "相机正在初始化"
                except (OSError,ValueError,urllib.error.URLError) as e:
                    last=str(e)
                time.sleep(.7)
            else:
                log=remote(c,f"tail -16 {REMOTE}/agent.log",timeout=5)
                raise RuntimeError("相机启动未成功："+last+"\n"+log[-1600:])
            self.data.mkdir(parents=True,exist_ok=True)
            self.config_path.write_text(json.dumps({"host":self.host,"token":self.token}),encoding="utf-8")
            with self.lock:
                self.state.update(host=self.host,busy=False,message="实时画面已连接",connected=True)
            self.stop=threading.Event()
            self.worker=threading.Thread(target=self._poll,args=(self.stop,),daemon=True)
            self.worker.start()
        except Exception as e:
            with self.lock:
                self.state.update(busy=False,connected=False,message=str(e))
        finally:
            if c:c.close()
            self.operation.release()

    def _poll(self, stop):
        while not stop.is_set():
            begin=time.monotonic()
            try:
                addresses=[self.host]
                if self.host in USB_HOSTS:
                    addresses.extend(a for a in USB_HOSTS if a!=self.host)
                failure=None
                for address in addresses:
                    if stop.is_set():break
                    try:
                        with self.request("/frame.jpg",timeout=1.5,host=address) as r:
                            frame=r.read(4_000_000)
                            meta=json.loads(base64.b64decode(r.headers["X-Frame-Meta"]))
                        break
                    except urllib.error.HTTPError as e:
                        if e.code in (401,403):
                            raise ValueError("相机连接凭据已变化，请点击重新连接") from e
                        failure=e
                    except (OSError,urllib.error.URLError) as e:
                        failure=e
                else:
                    raise failure
                if stop.is_set():break
                if not frame.startswith(b"\xff\xd8"):
                    raise ValueError("设备没有返回有效画面")
                if stop.is_set():break
                with self.lock:
                    # An HTTP reply may contain the last JPEG while capture or
                    # inference has stopped. Only a new frame renews freshness.
                    same_frame=(meta.get("seq")==self.meta.get("seq") and
                                meta.get("session_id")==self.meta.get("session_id") and
                                meta.get("device_id")==self.meta.get("device_id"))
                    if same_frame:
                        if time.time()-self.meta.get("received_at",0)>3:
                            raise ValueError("相机帧序号停止更新，请重新连接")
                    else:
                        meta["received_at"]=time.time()
                        self.host=address
                        self.frame,self.meta=frame,meta
                        self.state.update(connected=True,busy=False,host=address,message="实时画面已连接",mode=meta.get("mode","off"))
            except Exception as e:
                if stop.is_set():break
                with self.lock:
                    self.state.update(connected=False,message="取图中断："+str(e))
                stop.wait(.8)
            stop.wait(max(0,.09-(time.monotonic()-begin)))

    def set_mode(self, mode):
        if mode not in ("off","yolo11","red_blob","apriltag"):
            raise ValueError("未知的识别模式")
        with self.request("/mode",{"mode":mode},timeout=4) as response:
            return json.load(response)

    def disconnect(self):
        if not self.operation.acquire(blocking=False):
            raise ValueError("正在连接设备，请稍候")
        try:
            self.stop.set()
            if self.worker:self.worker.join(5)
            try:
                with self.request("/stop",{},timeout=2):pass
            except OSError:pass
            with self.lock:
                self.state.update(connected=False,message="已断开连接，设备将返回应用菜单")
        finally:
            self.operation.release()
