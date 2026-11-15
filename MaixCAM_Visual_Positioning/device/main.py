"""MaixCAM launcher entry; standalone data is independent of the installed app folder."""
import os
import secrets
import sys
from pathlib import Path

ROOT = Path("/root/aic_vision")
ROOT.mkdir(parents=True, exist_ok=True)
config = ROOT / "agent_config.json"
# A fresh launcher invocation must not inherit the previous process's lock FD.
# Only vision_agent's internal YOLO execv reuses that descriptor.
os.environ.pop("AIC_OWNER_FD", None)
if not config.exists():
    from ground import atomic_json
    atomic_json(config, {"token": secrets.token_urlsafe(32), "port": 18765,
                        "width": 640, "height": 480, "fps": 30, "display": True})
    config.chmod(0o600)
os.execv(sys.executable, [sys.executable, "-u", str(Path(__file__).with_name("vision_agent.py")),
                        "--config", str(config)])
