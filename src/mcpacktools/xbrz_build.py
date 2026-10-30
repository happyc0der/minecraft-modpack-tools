"""``mcpack xbrz``: build the optional xBRZ shared library, or show which scaler the tools will use.

xBRZ (by Zenju, with a C export by io mintz) is GPL-3.0 and is not part of this project. ``build`` downloads its four
source files from a pinned tag of https://github.com/iomintz/xbrz.py, checks their SHA-256, and compiles them with the
C++ compiler you have (g++, clang++ or MSVC's cl). The result stays on your machine.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from mcpacktools.scaling import default_xbrz_path, get_scaler

REPO = "iomintz/xbrz.py"
REF = "v1.0.2"
SOURCES = {  # file -> SHA-256 at REF
    "xbrz.cpp": "883fad2244ada34db073469cba3e96086e7bf5ad72ef6a2c953691dda58f3147",
    "xbrz.h": "2c214de1eac46bb7000a74fc7c710d8fb195bc46d2fcba7221bcb83cb50c9b1e",
    "xbrz_config.h": "8caf683926e268a8f56dccb742e8922f9d6f5a0269bd6d187debfa4641ae8fe2",
    "xbrz_tools.h": "fdd8ad61a49687cdb8c7ccae6a1fefbf87f018fd0c49432fe5f6ad84eab9c387",
}


def add_parser(sub) -> None:
    p = sub.add_parser("xbrz", help="build the optional xBRZ library, or show which scaler is in use",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["status", "build"])
    p.add_argument("--compiler", help="compiler to use (default: first of g++, clang++, cl found on PATH)")
    p.add_argument("--dest", type=Path, default=None, help=f"output library path (default: {default_xbrz_path()})")
    p.set_defaults(run=run)


def run(args) -> int:
    if args.action == "status":
        scaler = get_scaler("auto")
        where = getattr(scaler, "library", None)
        print(f"scaler: {scaler.name}" + (f" ({where})" if where else f" (no xBRZ library at {default_xbrz_path()})"))
        return 0
    return build(args.compiler, args.dest or default_xbrz_path())


def _download(tmp: Path) -> None:
    for name, expected in SOURCES.items():
        url = f"https://raw.githubusercontent.com/{REPO}/{REF}/lib/{name}"
        with urllib.request.urlopen(url, timeout=60) as r:
            data = r.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected:
            raise SystemExit(f"{name}: SHA-256 mismatch ({digest}); refusing to build")
        (tmp / name).write_bytes(data)
        print(f"  {name}  {len(data):>6} bytes  sha256 {digest[:16]}...")
    # xbrz.cpp #includes the Python-extension stub of xbrz.py; a plain shared library doesn't need it.
    (tmp / "dummy_module.cpp").write_text("// not building a Python extension\n")


def _command(compiler: str, src: Path, out: Path) -> list[str]:
    if Path(compiler).stem.lower() == "cl":
        return [compiler, "/nologo", "/O2", "/LD", "/EHsc", "/std:c++17", str(src), f"/Fe:{out}"]
    cmd = [compiler, "-O2", "-std=c++17", "-shared", "-o", str(out), str(src)]
    if sys.platform == "win32":
        cmd[1:1] = ["-static", "-static-libgcc", "-static-libstdc++"]  # no MinGW runtime DLLs needed beside it
    else:
        cmd.insert(1, "-fPIC")
    return cmd


def build(compiler: str | None, dest: Path) -> int:
    compiler = compiler or next((c for c in ("g++", "clang++", "cl") if shutil.which(c)), None)
    if not compiler:
        print("No C++ compiler found (g++, clang++ or cl). Install one, or keep using the built-in Scale2x scaler.")
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        print(f"downloading xBRZ sources from {REPO}@{REF} (GPL-3.0)")
        _download(tmp)
        out = tmp / dest.name
        cmd = _command(compiler, tmp / "xbrz.cpp", out)
        print("compiling:", " ".join(cmd))
        result = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
        if result.returncode != 0 or not out.is_file():
            print("compile failed:\n" + (result.stderr or result.stdout)[-4000:])
            return 1
        shutil.copy2(out, dest)
    print(f"built {dest}")
    return 0
