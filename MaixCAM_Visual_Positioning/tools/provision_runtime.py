"""Run Sipeed's own runtime installer on a USB-only, offline MaixCAM.

The PC provides a temporary SSH reverse TLS tunnel to the official service.
Device identities and downloaded device-bound binaries are never published.
Temporary hosts/network changes are reverted. Existing libraries are backed up.
"""
import argparse
import select
import shlex
import socket
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"host"))
from device import ssh_connect


def main():
    p=argparse.ArgumentParser();p.add_argument("--host",default="10.191.17.1")
    p.add_argument("--username",default="root");p.add_argument("--password",default="root")
    args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    c=ssh_connect(args.host,args.username,args.password,root/"data"/"known_hosts")
    proxy=urllib.request.getproxies().get("https")
    def relay(channel,origin,server):
        def run():
            sock=None
            try:
                if proxy:
                    addr=urllib.parse.urlsplit(proxy)
                    sock=socket.create_connection((addr.hostname,addr.port or 80),10)
                    sock.sendall(b"CONNECT maixvision.sipeed.com:443 HTTP/1.1\r\nHost: maixvision.sipeed.com:443\r\n\r\n")
                    head=b""
                    while not head.endswith(b"\r\n\r\n"):
                        part=sock.recv(1)
                        if not part:raise OSError("proxy closed")
                        head+=part
                    if b" 200 " not in head.split(b"\r\n")[0]:raise OSError("proxy CONNECT failed")
                else:sock=socket.create_connection(("maixvision.sipeed.com",443),12)
                while not channel.closed:
                    ready,_,_=select.select([sock,channel],[],[],10)
                    for source in ready:
                        data=source.recv(65536)
                        if not data:return
                        (channel if source is sock else sock).sendall(data)
            except OSError:pass
            finally:
                channel.close()
                if sock:sock.close()
        threading.Thread(target=run,daemon=True).start()
    sftp=None;hosts=None
    try:
        c.get_transport().request_port_forward("127.0.0.1",443,handler=relay)
        sftp=c.open_sftp();hosts=sftp.open("/etc/hosts","rb").read()
        with sftp.open("/etc/hosts","wb") as f:f.write(hosts+b"\n127.0.0.1 maixvision.sipeed.com\n")
        # Old Sipeed settings requires a WLAN address even when TLS is tunneled.
        # A TEST-NET /32 address enables that check without changing Wi-Fi config.
        code=r'''
import os,shutil,subprocess,json
backup='/root/aic_vision/runtime-backup'
os.makedirs(backup,exist_ok=True)
paths=['/maixapp/maixcam_lib.version','/usr/lib/libmaixcam_lib.so.1.22.0','/usr/lib/libmaixcam_lib.so.1.22.0_1']
for p in paths:
 if os.path.exists(p) and not os.path.exists(backup+'/'+os.path.basename(p)):shutil.copy2(p,backup)
if not os.path.exists(backup+'/links.json'):
 open(backup+'/links.json','w').write(json.dumps({p:os.readlink(p) for p in ['/usr/lib/libmaixcam_lib.so','/lib/libmaixcam_lib.so']}))
temporary_ip=False
temporary_route=False
try:
 result=subprocess.run(['ip','-4','addr','show','dev','wlan0'],capture_output=True)
 if b'inet ' not in result.stdout:
  subprocess.run(['ip','addr','add','192.0.2.1/32','dev','wlan0'],check=True)
  temporary_ip=True
 routes=subprocess.run(['ip','route'],capture_output=True).stdout
 if b'default ' not in routes:
  subprocess.run(['ip','route','add','default','via','192.0.2.2','dev','wlan0','onlink','metric','42760'],check=True)
  temporary_route=True
 if os.path.exists('/maixapp/maixcam_lib.version'):os.unlink('/maixapp/maixcam_lib.version')
 result=subprocess.run(['/maixapp/apps/settings/settings','install_runtime'],capture_output=True,timeout=50)
 print('INSTALL_EXIT',result.returncode,flush=True)
 for line in (result.stdout+result.stderr).decode(errors='replace').splitlines():
  if not any(s in line.lower() for s in ['uid=','device_key','key:','key =']):print(line,flush=True)
finally:
 if temporary_route:subprocess.run(['ip','route','del','default','via','192.0.2.2','dev','wlan0','metric','42760'])
 if temporary_ip:subprocess.run(['ip','addr','del','192.0.2.1/32','dev','wlan0'])
 if not os.path.exists('/maixapp/maixcam_lib.version'):shutil.copy2(backup+'/maixcam_lib.version','/maixapp/maixcam_lib.version')
print('RUNTIME_VERSION',open('/maixapp/maixcam_lib.version').read().strip(),flush=True)
raise SystemExit(0 if result.returncode==0 else 1)
'''
        _,out,err=c.exec_command("python3 -u -c "+shlex.quote(code),timeout=58)
        for line in out:print(line.rstrip(),flush=True)
        error=err.read().decode(errors="replace")
        if error:print(error[-600:])
        return out.channel.recv_exit_status()
    finally:
        if sftp and hosts is not None:
            with sftp.open("/etc/hosts","wb") as f:f.write(hosts)
            sftp.close()
        try:c.get_transport().cancel_port_forward("127.0.0.1",443)
        except Exception:pass
        c.close()
        print("Temporary connection settings restored.",flush=True)


if __name__=="__main__":sys.exit(main())
