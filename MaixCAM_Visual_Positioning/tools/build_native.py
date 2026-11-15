"""Build a launcher app ZIP without local calibration, tokens or device-bound runtime."""
import hashlib
import json
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
FILES = ("app.yaml", "main.py", "vision_agent.py", "native_ui.py", "ground.py")


def build():
    icon = ROOT / "device" / "icon.png"
    im = Image.new("RGB", (96, 96), "#08150e")
    d = ImageDraw.Draw(im)
    d.rectangle((13, 13, 82, 82), outline="#44ee99", width=3)
    for p in (30, 48, 66):
        d.line((p, 14, p, 82), fill="#206442")
        d.line((14, p, 82, p), fill="#206442")
    d.ellipse((40, 40, 56, 56), fill="#44ee99")
    im.save(icon)
    output = ROOT / "MaixGround-2.1.1.zip"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        for name in (*FILES, "icon.png"):
            z.write(ROOT / "device" / name, name)
        z.write(ROOT / "host" / "geometry.py", "geometry.py")
    with zipfile.ZipFile(output) as z:
        manifest = {name: hashlib.sha256(z.read(name)).hexdigest() for name in z.namelist()}
    result = {"version": "2.1.1", "package": output.name,
              "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "files": manifest}
    (ROOT / "docs" / "native-package.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "files"}))
    return output


if __name__ == "__main__": build()
