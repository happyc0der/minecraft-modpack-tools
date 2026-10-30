"""``mcpack upscale``: give every mod texture that your HD pack lacks an upscaled version.

HD packs like Faithful cover vanilla and a list of popular mods, but a big modpack always has textures they miss, and
those stay at 16x next to 32x/64x neighbours. This command takes every texture from the mod jars (and optionally the
game jar) that the ``--skip`` packs don't already cover, upscales it with the rules in :mod:`mcpacktools.scaling`
(animation strips frame by frame, tiling textures seamless, cut-outs kept cut-out, alpha bleed), and writes:

* ``--out NEW.zip``: a separate "fill" pack. Put it **below** your HD pack in the resource-pack list, so real HD art
  always wins. Use this for 1.6.1 and newer, where packs stack.
* ``--merge-into BASE.zip --out NEW.zip``: a copy of BASE with the upscaled textures added. Use this for 1.4/1.5,
  which can load only one texture pack (add ``--legacy``: those mods keep textures anywhere in their jar).

Textures larger than ``--max-size`` are skipped: they are model or GUI art that is already high-res.
"""

from __future__ import annotations

import argparse
import time
from multiprocessing import Pool
from pathlib import Path

from mcpacktools.packfiles import (SourceIndex, is_legacy_texture, is_texture, pack_mcmeta, png_size, read_zip,
                                   write_zip)
from mcpacktools.scaling import LEAVES, decode, encode, get_scaler, upscale_strip
from mcpacktools.textures.animation import geometry, legacy_strip

SKIPPED_DIRS = ("/font/", "/colormap/")


def add_parser(sub) -> None:
    p = sub.add_parser("upscale", help="upscale every mod texture your HD pack lacks into a fill pack",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mods", type=Path, required=True, help="the instance's mods folder")
    p.add_argument("--vanilla", type=Path, help="also cover vanilla textures from this client jar")
    p.add_argument("--skip", type=Path, nargs="*", default=[], help="packs whose textures win (left out of the fill pack)")
    p.add_argument("--merge-into", type=Path, help="copy this pack and add the upscaled textures to it")
    p.add_argument("--out", type=Path, required=True, help="output zip")
    p.add_argument("--factor", type=int, default=4, help="scale factor (default 4: 16x -> 64x)")
    p.add_argument("--max-size", type=int, default=256, help="skip textures wider or taller than this (default 256)")
    p.add_argument("--legacy", action="store_true", help="1.4/1.5 mods: any .png in the jar is a texture")
    p.add_argument("--pack-format", type=int, help="pack_format for a new fill pack's pack.mcmeta")
    p.add_argument("--description", default="Mod textures upscaled with mcpack. Load below your HD pack.")
    p.add_argument("--scaler", choices=["auto", "xbrz", "scale2x", "nearest"], default="auto")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--jobs", type=int, default=None, help="worker processes (default: CPU count)")
    p.set_defaults(run=run)


def wanted(name: str, legacy: bool) -> bool:
    if not name.endswith(".png") or any(d in name for d in SKIPPED_DIRS) or name.startswith("font/"):
        return False
    return is_legacy_texture(name) if legacy else is_texture(name)


def legacy_meta_name(name: str) -> str:
    """1.4/1.5 animation descriptor: ``foo.png`` -> ``foo.txt``."""
    return name[:-4] + ".txt"


def _work(job):
    name, png, meta, legacy, factor, max_size, scaler = job
    size = png_size(png)
    if not size or size[0] == 0:
        return name, None, "empty or not a PNG"
    if max(size) > max_size:
        return name, None, "too large"
    try:
        geo = (legacy_strip(png) if legacy else geometry(png, meta)) if meta else None
        img = decode(png)
        frame_h = geo[1] if geo else img.shape[0]
        out = upscale_strip(img, frame_h, factor, scaler, leaves=bool(LEAVES.search(name)))
        return name, encode(out), None
    except Exception as e:  # a broken texture in one mod must not stop the build
        return name, None, f"{type(e).__name__}: {e}"


def run(args) -> int:
    t0 = time.time()
    if not args.merge_into and args.pack_format is None:
        raise SystemExit("--pack-format is required for a new fill pack (or use --merge-into)")
    accept = is_legacy_texture if args.legacy else is_texture
    sources = SourceIndex.build(args.vanilla, args.mods, accept)
    files = read_zip(args.merge_into) if args.merge_into else {}
    covered = set(files)
    for pack in args.skip:
        covered |= set(read_zip(pack))
    todo = [n for n in sources.names() if wanted(n, args.legacy) and n not in covered]
    scaler = get_scaler(args.scaler)
    print(f"{len(todo)} textures to upscale x{args.factor} with {scaler.name} (from {len(sources.archives)} archives)")
    if args.dry_run:
        for name in sorted(todo)[:50]:
            print(" ", name)
        return 0

    meta_name = legacy_meta_name if args.legacy else (lambda n: n + ".mcmeta")
    jobs = [(n, sources.read(n), sources.read(meta_name(n)), args.legacy, args.factor, args.max_size, scaler) for n in todo]
    added, skipped = 0, {}
    with Pool(args.jobs) as pool:
        for name, png, why in pool.imap_unordered(_work, jobs, chunksize=32):
            if png is None:
                skipped[why] = skipped.get(why, 0) + 1
                continue
            files[name] = png
            added += 1
            meta = sources.read(meta_name(name))
            if meta is not None and meta_name(name) not in files:
                files[meta_name(name)] = meta
    if not args.merge_into:
        files["pack.mcmeta"] = pack_mcmeta(args.pack_format, args.description)
    write_zip(args.out, files, level=3)
    print(f"wrote {args.out}: +{added} textures, skipped {skipped}, {time.time() - t0:.0f}s")
    return 0
