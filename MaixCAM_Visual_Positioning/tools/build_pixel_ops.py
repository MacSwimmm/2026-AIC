"""Rebuild the MaixCAM RISC-V pixel helper with ziglang==0.13.0 (pip)."""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    source = ROOT / 'device' / 'pixel_ops.c'
    output = ROOT / 'device' / '_pixel_ops.so'
    subprocess.run([
        sys.executable, '-m', 'ziglang', 'cc',
        '-target', 'riscv64-linux-musl', '-mcpu=generic_rv64+m+a+f+d+c',
        '-O3', '-fPIC', '-shared', '-nostdlib', '-fno-builtin',
        '-fno-sanitize=all', '-fno-stack-protector', '-g0',
        '-Wl,--build-id=none', '-Wl,-s', str(source), '-o', str(output)
    ], check=True)
    print(output.name, output.stat().st_size, hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    build()
