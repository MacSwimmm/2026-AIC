"""Check the shipped memory-only converter on MaixCAM using synthetic pixels."""
import hashlib
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from device import ssh_connect, remote

CODE = r'''
import sys,json,hashlib
sys.path.insert(0,'/root/aic_vision')
from pixel_convert import Pixels
from maix import image
import numpy as np
p=Pixels()
# Uniform black/white/primary blocks have known BT.601 limited-range values.
colors=[(0,0,0),(255,255,255),(255,0,0),(0,255,0),(0,0,255)]
expected=[(16,128,128),(235,128,128),(82,240,90),(144,34,54),(41,110,240)]
max_error=0
for color,(y,v,u) in zip(colors,expected):
 f=image.from_bytes(8,8,image.Format.FMT_RGB888,bytes(color)*64,copy=True)
 nv=p.nv21(f);raw=nv.to_bytes()
 assert set(raw[:64])=={y},(color,list(raw[:8]))
 assert raw[64:]==bytes((v,u))*16
 back=p.rgb(nv).to_bytes()
 error=max(abs(back[i]-color[i%3]) for i in range(len(back)))
 max_error=max(max_error,error);assert error<=2,(color,error)
# Validate a non-uniform NV21 frame against OpenCV's independent conversion.
import cv2
rng=np.random.default_rng(731)
raw=rng.integers(0,256,(48*3//2,64),dtype=np.uint8)
f=image.from_bytes(64,48,image.Format.FMT_YVU420SP,raw.tobytes(),copy=True)
actual=np.frombuffer(p.rgb(f).to_bytes(),dtype=np.uint8).reshape(48,64,3)
reference=cv2.cvtColor(raw,cv2.COLOR_YUV2RGB_NV21)
delta=np.abs(actual.astype(int)-reference.astype(int))
assert delta.max()<=1,int(delta.max())
# Reuse and release temporary input buffers; returned images own their pixels.
saved=p.nv21(image.from_bytes(8,8,image.Format.FMT_RGB888,bytes((255,0,0))*64,copy=True))
for i in range(30):p.rgb(saved)
assert saved.to_bytes()[:64]==bytes((82,))*64
print(json.dumps({'known_color_max_error':max_error,'opencv_max_error':int(delta.max()),
 'temporary_buffer_lifetime_checked':True,'passed':True}))
'''


if __name__ == '__main__':
    c = ssh_connect('10.191.17.1', 'root', 'root', ROOT / 'data' / 'known_hosts')
    try:
        result = json.loads(remote(c, 'python3 -c ' + shlex.quote(CODE), timeout=20).splitlines()[-1])
    finally:
        c.close()
    result.update(tested_at_utc=datetime.now(timezone.utc).isoformat(),
                  binary_sha256=hashlib.sha256((ROOT / 'device/_pixel_ops.so').read_bytes()).hexdigest(),
                  test_type='Synthetic pixels on real device; no detector input conversion')
    (ROOT / 'docs/pixel-conversion-validation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
