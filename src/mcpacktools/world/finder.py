"""``mcpack worlds``: find every copy of every world under some folders (also inside backup zips) and compare.

A world is identified by its seed and its name, both read from level.dat; the copy played last is the newest. This
answers "which of these old worlds don't I have anywhere else?" after a disk recovery, or before deleting backups.

    mcpack worlds D:/old-drive                      # list worlds, newest copy of each, and how many copies
    mcpack worlds D:/old-drive --against ~/games    # which old worlds exist only there, or are newer there
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

from mcpacktools import nbt

SKIP_DIRS = {".git", "node_modules", "libraries", "assets", "mods", "resourcepacks", "shaderpacks", "texturepacks"}


@dataclass
class WorldCopy:
    path: str           # folder, or zip file
    inner: str | None   # path of level.dat inside the zip
    name: str
    seed: int | None
    last_played: int    # ms since the epoch

    @property
    def key(self) -> tuple:
        return self.seed, self.name.strip().lower()

    def where(self) -> str:
        return f"{self.path} :: {self.inner}" if self.inner else self.path


def add_parser(sub) -> None:
    p = sub.add_parser("worlds", help="find all copies of every world (folders and zips) and compare locations",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("roots", type=Path, nargs="+", help="folders to search")
    p.add_argument("--against", type=Path, nargs="*", default=[], help="compare with the worlds under these folders")
    p.add_argument("--no-zips", action="store_true", help="don't look inside zip files")
    p.set_defaults(run=run)


def level_info(raw: bytes) -> tuple[str, int | None, int] | None:
    """(LevelName, seed, LastPlayed) from level.dat bytes (gzipped or not)."""
    try:
        data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        root = nbt.loads(data).value.get("Data")
    except (OSError, ValueError, EOFError, AttributeError):
        return None
    if root is None:
        return None
    seed = root.get("RandomSeed")
    if seed is None:  # 1.16+
        settings = root.get("WorldGenSettings")
        seed = settings.get("seed") if settings is not None else None
    name = root.get("LevelName", b"")
    return name.decode("utf-8", "replace"), seed, root.get("LastPlayed", 0) or 0


def scan(roots: list[Path], zips: bool = True) -> list[WorldCopy]:
    found = []
    for root in roots:
        for cur, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            if "level.dat" in files:
                try:
                    info = level_info(Path(cur, "level.dat").read_bytes())
                except OSError:
                    info = None
                if info:
                    found.append(WorldCopy(cur, None, *info))
                dirs[:] = []  # a world's own sub-folders hold no other worlds
            if zips:
                for f in files:
                    if f.lower().endswith(".zip"):
                        found += _scan_zip(Path(cur, f))
    return found


def _scan_zip(path: Path) -> list[WorldCopy]:
    out = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.replace("\\", "/").split("/")[-1] == "level.dat":
                    info = level_info(z.read(name))
                    if info:
                        out.append(WorldCopy(str(path), name, *info))
    except (OSError, zipfile.BadZipFile):
        pass
    return out


def newest(copies: list[WorldCopy]) -> dict[tuple, WorldCopy]:
    best: dict[tuple, WorldCopy] = {}
    for c in copies:
        if c.key not in best or c.last_played > best[c.key].last_played:
            best[c.key] = c
    return best


def when(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M") if ms else "?"


def run(args) -> int:
    copies = scan(args.roots, not args.no_zips)
    groups = newest(copies)
    count = {k: sum(1 for c in copies if c.key == k) for k in groups}
    print(f"{len(groups)} worlds ({len(copies)} copies) under {', '.join(map(str, args.roots))}")
    if not args.against:
        for k, c in sorted(groups.items(), key=lambda kv: kv[1].name.lower()):
            print(f"  {c.name:<28} last played {when(c.last_played)}  copies {count[k]}  seed {c.seed}  {c.where()}")
        return 0

    other = newest(scan(args.against, not args.no_zips))
    only, newer, covered = [], [], []
    for k, c in sorted(groups.items(), key=lambda kv: kv[1].name.lower()):
        o = other.get(k)
        if o is None:
            only.append(c)
        elif c.last_played > o.last_played + 60_000:
            newer.append((c, o))
        else:
            covered.append((c, o))
    print(f"\nONLY HERE ({len(only)}):")
    for c in only:
        print(f"  {c.name:<28} last played {when(c.last_played)}  seed {c.seed}  {c.where()}")
    print(f"\nNEWER HERE than the other copy ({len(newer)}):")
    for c, o in newer:
        print(f"  {c.name:<28} {when(c.last_played)} vs {when(o.last_played)}  {c.where()}  <->  {o.where()}")
    print(f"\nALSO THERE, same age or newer ({len(covered)}):")
    for c, o in covered:
        print(f"  {c.name:<28} {when(c.last_played)} / {when(o.last_played)}  there: {o.where()}")
    return 0
