"""Install/update the app menu entry over USB SSH; never import PC test calibration."""
import argparse
import json
import os
import shlex
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
from device import ssh_connect, remote

INSTALL = r'''
import configparser,json,os,secrets,shutil,sys,time,zipfile
from pathlib import Path
stage=Path(sys.argv[1]); dest=Path('/maixapp/apps/aic_ground'); shared=Path('/root/aic_vision')
shared.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(str(stage)+'.zip') as z:
    names=z.namelist()
    allowed={'app.yaml','main.py','vision_agent.py','ground.py','native_ui.py','geometry.py','icon.png'}
    if set(names)!=allowed:raise RuntimeError('Invalid application package')
    z.extractall(stage)
sys.path.insert(0,str(stage))
from vision_agent import prepare_device
from ground import atomic_json
prepare_device(str(shared))
backup=shared/'app-backups'/time.strftime('%Y%m%d-%H%M%S')
backup.mkdir(parents=True,exist_ok=True)
info=Path('/maixapp/apps/app.info')
shutil.copy2(info,backup/'app.info')
if dest.exists():shutil.move(str(dest),str(backup/'aic_ground'))
shutil.move(str(stage),str(dest))
# Preserve existing launcher sections byte-for-byte; replace only our own section.
text=info.read_text(encoding='utf-8')
import re
text=re.sub(r'(?ms)^\[aic_ground\]\n.*?(?=^\[|\Z)','',text)
text+='\n[aic_ground]\nname=Ground Positioning\nname[zh]=地面定位\nversion=2.1.1\nicon=/maixapp/apps/aic_ground/icon.png\nexec=main.py\nauthor=MacSwimmm\ndesc=Local ground calibration and targets\ndesc[zh]=本机地面标定和目标点保存\n'
temp=info.with_suffix('.next');temp.write_text(text,encoding='utf-8');os.replace(temp,info)
cfgpath=shared/'agent_config.json'
cfg=json.loads(cfgpath.read_text()) if cfgpath.exists() else {'token':secrets.token_urlsafe(32)}
cfg.update(port=18765,width=640,height=480,fps=30,display=True,initial_mode='off')
atomic_json(cfgpath,cfg);os.chmod(cfgpath,0o600)
# Keep the SSH deployment entry in sync with the installed launcher application.
for name in ('vision_agent.py','native_ui.py','ground.py','geometry.py'):
    shutil.copy2(dest/name,shared/name)
print(json.dumps({'installed':True,'application_id':'aic_ground','version':'2.1.1',
 'data_path':'/root/aic_vision/data/ground.json','existing_device_data_preserved':True,
 'pc_test_calibration_imported':False,'backup':str(backup)}))
'''


def main():
    p = argparse.ArgumentParser(description="安装 MaixCAM 本机地面定位应用")
    p.add_argument("--host", default="10.191.17.1")
    p.add_argument("--username", default="root")
    p.add_argument("--start", action="store_true", help="安装后立即显示本机应用")
    args = p.parse_args()
    package = ROOT / "MaixGround-2.1.1.zip"
    if not package.exists(): raise RuntimeError("请先运行 python tools/build_native.py")
    c = ssh_connect(args.host, args.username, os.environ.get("MAIXCAM_PASSWORD", "root"), ROOT / "data" / "known_hosts")
    stage = "/root/aic_vision/install-" + uuid.uuid4().hex[:12]
    try:
        remote(c, "mkdir -p /root/aic_vision; chmod 700 /root/aic_vision")
        sftp = c.open_sftp()
        try: sftp.put(str(package), stage + ".zip")
        finally: sftp.close()
        print(remote(c, "/usr/bin/python3 -c " + shlex.quote(INSTALL) + " " + shlex.quote(stage), timeout=40))
        if args.start:
            print(remote(c, "cd /maixapp/apps/aic_ground && nohup /usr/bin/python3 -u main.py >/root/aic_vision/agent.log 2>&1 </dev/null &"))
        else:
            remote(c, "nohup /maixapp/apps/launcher/launcher_daemon >/dev/null 2>&1 </dev/null &")
    finally: c.close()


if __name__ == "__main__": main()
