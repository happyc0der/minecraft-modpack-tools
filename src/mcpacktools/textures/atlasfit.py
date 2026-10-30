"""``mcpack atlas-fit``: shrink oversized textures so an HD pack fits the game's texture atlas.

Before 1.13, every block and item texture of every mod is stitched into ONE atlas, at most 16384 x 16384 pixels on
most GPUs (the game also rounds each sprite up to a multiple of 2^mipmap_levels). An HD pack that upscales thousands of
textures, or contains some 512-2048 px ones, overflows it and the game crashes at start-up with
``StitcherException: Unable to fit: <texture>``.

This command estimates how much of the atlas the pack's sprites take, and with a cap given, scales every block/item
texture wider than the cap down to it (Lanczos; cut-out alpha stays binary; aspect ratio kept, so animation strips
keep square frames). Some mods stitch other textures into the same atlas; add their folders with ``--extra``
(for example Betweenlands packs its model textures: ``assets/thebetweenlands/textures/tiles/``).
"""

from __future__ import annotations

import argparse
import io
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

from mcpacktools.packfiles import png_size, read_zip, write_zip
from mcpacktools.scaling import encode, is_binary_alpha

SPRITE_DIRS = ("/textures/blocks/", "/textures/items/", "/textures/block/", "/textures/item/")
ATLAS_LIMIT = 16384 * 16384


def add_parser(sub) -> None:
    p = sub.add_parser("atlas-fit", help="shrink oversized textures so a pack fits the texture atlas",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pack", type=Path, help="resource pack zip (rewritten in place unless --dry-run)")
    p.add_argument("--cap", type=int, default=None, help="max width for block/item textures, e.g. 128 (default: report only)")
    p.add_argument("--extra", nargs="*", default=[], metavar="PATH_PREFIX",
                   help="other texture folders stitched into the atlas, capped at --extra-cap")
    p.add_argument("--extra-cap", type=int, default=256)
    p.add_argument("--mipmap", type=int, default=4, help="mipmap levels the game uses (sprites round up to 2^n)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--jobs", type=int, default=None)
    p.set_defaults(run=run)


def is_sprite(name: str) -> bool:
    return name.startswith("assets/") and name.endswith(".png") and any(d in name for d in SPRITE_DIRS)


def atlas_area(files: dict[str, bytes], mipmap: int, extra: tuple[str, ...] = ()) -> int:
    """Upper bound of the atlas area the pack's sprites need (each side rounded up to 2^mipmap, one frame per strip)."""
    step = 2 ** mipmap
    total = 0
    for name, data in files.items():
        if not (is_sprite(name) or name.startswith(extra)):
            continue
        size = png_size(data)
        if size:
            w, h = size
            total += (-(-w // step) * step) * (-(-min(w, h) // step) * step)
    return total


def shrink(job):
    name, data, cap = job
    try:
        w, h = png_size(data)
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        nh = max(1, round(h * cap / w))
        src = np.array(img)
        out = np.array(img.resize((cap, nh), Image.LANCZOS))
        if is_binary_alpha(src[:, :, 3]):
            out[:, :, 3] = np.array(Image.fromarray(src[:, :, 3]).resize((cap, nh), Image.NEAREST))
        return name, encode(out), None
    except Exception as e:
        return name, None, f"{type(e).__name__}: {e}"


def run(args) -> int:
    t0 = time.time()
    files = read_zip(args.pack)
    extra = tuple(args.extra)
    before = atlas_area(files, args.mipmap, extra)
    print(f"{args.pack.name}: sprites need up to {before / 1e6:.0f}M px = {before / ATLAS_LIMIT:.0%} of a 16384^2 atlas")
    if args.cap is None:
        return 0
    jobs = []
    for name, data in files.items():
        size = png_size(data)
        cap = args.extra_cap if name.startswith(extra) else args.cap if is_sprite(name) else None
        if cap and size and size[0] > cap:
            jobs.append((name, data, cap))
    print(f"{len(jobs)} textures wider than their cap ({args.cap} px; extra folders {args.extra_cap} px)")
    if args.dry_run:
        return 0
    errors = 0
    with Pool(args.jobs) as pool:
        for name, png, err in pool.imap_unordered(shrink, jobs, chunksize=16):
            if err:
                errors += 1
                print(f"  skipped {name}: {err}")
            else:
                files[name] = png
    after = atlas_area(files, args.mipmap, extra)
    write_zip(args.pack, files)
    print(f"capped {len(jobs) - errors}, errors {errors}; now up to {after / ATLAS_LIMIT:.0%} of the atlas; {time.time() - t0:.0f}s")
    return 0
