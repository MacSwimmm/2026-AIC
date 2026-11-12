"""Check bundled resources/bytecode against reviewed sources and emit a manifest."""
import hashlib
import json
import marshal
import sys
import types
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


def normalized(code):
    return code.replace(co_filename="",co_consts=tuple(normalized(x) if isinstance(x,types.CodeType) else x for x in code.co_consts))


def main():
    root=Path(__file__).resolve().parents[1]
    exe=root/"MaixVisionConsole.exe";archive=CArchiveReader(str(exe))
    names={n.replace("\\","/"):n for n in archive.toc}
    checked=[]
    for p in [*(root/"host"/"web").rglob("*"),root/"device"/"vision_agent.py"]:
        if not p.is_file():continue
        rel=p.relative_to(root).as_posix()
        assert archive.extract(names[rel])==p.read_bytes(),rel
        checked.append(rel)
    pyz=archive.open_embedded_archive('PYZ.pyz')
    for name in ['device','geometry']:
        compiled=compile((root/"host"/(name+".py")).read_text(encoding='utf-8'),"",'exec')
        assert normalized(pyz.extract(name))==normalized(compiled),name
    main_code=marshal.loads(archive.extract('server'))
    assert normalized(main_code)==normalized(compile((root/'host'/'server.py').read_text(encoding='utf-8'),"",'exec')),'server'
    sources=[*(root/'host').rglob('*.py'),*(root/'device').glob('*.py'),*(root/'tools').glob('*.py'),*(root/'host'/'web').rglob('*')]
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sources if p.is_file()}
    report={'application':'maixcam-ground-console','version':'1.0.0','exe_bytes':exe.stat().st_size,'exe_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'bundled_resources_equal_source':True,'compiled_host_modules_equal_source':True,'resources_checked':checked,'source_sha256':hashes}
    (root/'docs'/'release-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('source_sha256','resources_checked')},indent=2))


if __name__=='__main__':main()
