"""``mcpack animated``: rebuild every animated texture in an HD pack, frame by frame.

Typical damage this repairs, in packs where animation strips were upscaled as one image (or copied from low-res art):

* frames bleed into each other at the frame borders (specks at the top of each frame, seams in lava and portals);
* cut-out animations (fire) get blocky or semi-transparent edges;
* hand-drawn art from a lower-resolution base pack (for example Faithful 32x) is thrown away in favour of a
  smoothed-out 16x original, so flames turn into flat white blobs.

For each texture that has an animation .mcmeta, the source art is chosen like this:

1. a **base pack** (``--base``, e.g. Faithful 32x) if it has the texture with the same frame count, at least the
   original's width, and matching art (a guard against version drift; for fire only the average colour must match);
2. otherwise the **original** from the game or mod jar, but only if the pack's current strip is itself just an
   upscale of that original. Anything else is hand-made art and is kept as it is.

The chosen art is scaled frame by frame to at least ``--target`` pixels wide (see :mod:`mcpacktools.scaling` for the
rules), and the source's .mcmeta is written beside it. Textures whose source is already that wide are left alone.

Fire gets one extra step (turn it off with ``--no-warm``): pixels close to pure white are tinted towards the fire's
own brightest colour. Upscaling turns a flame's few white pixels into large flat white shapes; tinting keeps the hot
core bright but makes it read as flame.
"""

from __future__ import annotations

import argparse
import math
import re
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from mcpacktools.packfiles import SourceIndex, read_zip, write_zip
from mcpacktools.scaling import LEAVES, decode, encode, get_scaler, upscale_strip
from mcpacktools.textures.animation import animation, geometry

FIRE = re.compile(r"fire", re.IGNORECASE)
NOT_FIRE = re.compile(
    r"firework|fireproof|firefl(y|ies)|fire_coral|fire_charge|campfire_log|fire_resist|firebrick|fire_?stone|"
    r"fireball|firewood|fire_essence|fire_seeds|fire_crop|charm_fire|lens_fire|rod_fire", re.IGNORECASE)

# 1.13+ texture paths -> the 1.12 names, for using a 1.12 base pack on a newer game (--modern-names).
MODERN_TO_LEGACY = {
    "block/fire_0": "blocks/fire_layer_0", "block/fire_1": "blocks/fire_layer_1",
    "block/lava_still": "blocks/lava_still", "block/lava_flow": "blocks/lava_flow",
    "block/water_still": "blocks/water_still", "block/water_flow": "blocks/water_flow",
    "block/nether_portal": "blocks/portal", "block/sea_lantern": "blocks/sea_lantern",
    "block/magma_block": "blocks/magma", "block/prismarine": "blocks/prismarine_rough",
}


def add_parser(sub) -> None:
    p = sub.add_parser("animated", help="rebuild every animated texture of an HD pack, frame by frame",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pack", type=Path, help="resource pack zip to fix (rewritten in place)")
    p.add_argument("--vanilla", type=Path, required=True, help="the game's client jar (original vanilla textures)")
    p.add_argument("--mods", type=Path, help="mods folder (original mod textures)")
    p.add_argument("--base", type=Path, nargs="*", default=[], help="lower-res base pack(s) with hand-drawn frames, best first")
    p.add_argument("--base-vanilla-only", action="store_true", help="use base packs for minecraft: textures only "
                   "(when the base pack targets a different game version than the mods)")
    p.add_argument("--modern-names", action="store_true", help="map 1.13+ texture paths to 1.12 names in base packs")
    p.add_argument("--target", type=int, default=64, help="minimum frame width after scaling (default 64)")
    p.add_argument("--scaler", choices=["auto", "xbrz", "scale2x", "nearest"], default="auto")
    p.add_argument("--only", help="regex: only textures whose path matches")
    p.add_argument("--no-warm", action="store_true", help="keep fire's white core exactly as drawn")
    p.add_argument("--preview", type=Path, help="write a before/after PNG sheet of the first 10 rebuilt textures")
    p.add_argument("--dry-run", action="store_true", help="report what would be rebuilt; write nothing")
    p.add_argument("--jobs", type=int, default=None, help="worker processes (default: CPU count)")
    p.set_defaults(run=run)


# --------------------------------------------------------------------------------------------- source choice


def _first_frame(png: bytes, frame_h: int) -> np.ndarray:
    return decode(png)[:frame_h].astype(np.float32)


def _shrink(img: np.ndarray, h: int, w: int) -> np.ndarray | None:
    """Box-average ``img`` down to h x w; None if the sizes aren't an integer multiple."""
    H, W = img.shape[:2]
    if H % h or W % w or H // h != W // w:
        return None
    k = W // w
    return img.reshape(h, k, w, k, 4).mean(axis=(1, 3))


def same_art(base_png: bytes, base_geo, orig_png: bytes | None, orig_geo, fire: bool) -> bool:
    """Does the base pack's texture depict the same thing as the original? (Mods redraw textures between versions.)

    Compared on the first frame: silhouette overlap >= 0.75 and mean colour difference <= 48 on shared opaque pixels.
    Base packs redraw fire freely, so for fire only the average colour must agree (difference <= 60).
    """
    if not orig_png:
        return True
    try:
        b = _first_frame(base_png, base_geo[1])
        o = _first_frame(orig_png, orig_geo[1])
    except OSError:
        return False
    if fire:
        bc, oc = b[b[:, :, 3] >= 128][:, :3], o[o[:, :, 3] >= 128][:, :3]
        return len(bc) > 0 and len(oc) > 0 and float(np.abs(bc.mean(axis=0) - oc.mean(axis=0)).mean()) <= 60
    small = _shrink(b, o.shape[0], o.shape[1])
    if small is None:
        return False
    sa, oa = small[:, :, 3] >= 128, o[:, :, 3] >= 128
    union = (sa | oa).sum()
    if union and (sa & oa).sum() / union < 0.75:
        return False
    both = sa & oa
    return not both.any() or float(np.abs(small[:, :, :3] - o[:, :, :3])[both].mean()) <= 48


def is_upscale_of(pack_png: bytes, orig_png: bytes, orig_geo) -> bool:
    """Is the pack's strip just an upscale of the original (same frame count, first frame matches when shrunk)?"""
    try:
        p = decode(pack_png).astype(np.float32)
        o = _first_frame(orig_png, orig_geo[1])
    except OSError:
        return False
    k = p.shape[1] // o.shape[1]
    if k < 1 or p.shape[1] % o.shape[1] or p.shape[0] != orig_geo[2] * orig_geo[1] * k:
        return False
    small = _shrink(p[:orig_geo[1] * k], o.shape[0], o.shape[1])
    if small is None:
        return False
    sa, oa = small[:, :, 3] >= 128, o[:, :, 3] >= 128
    if (sa == oa).mean() < 0.85:
        return False
    both = sa & oa
    return not both.any() or float(np.abs(small[:, :, :3] - o[:, :, :3])[both].mean()) <= 20


def is_fire(path: str) -> bool:
    return bool(FIRE.search(path.rsplit("/", 1)[-1]) and not NOT_FIRE.search(path))


# --------------------------------------------------------------------------------------------- rebuilding


def warm_white(strip: np.ndarray, original: np.ndarray) -> np.ndarray:
    """Tint near-white pixels towards the flame's brightest non-white colour (weight 0 at min channel 225, 0.75 at 255)."""
    visible = original[original[:, :, 3] > 0][:, :3].astype(np.float32)
    coloured = visible[visible.min(axis=1) < 225] if len(visible) else visible
    if not len(coloured):
        return strip
    luma = coloured @ np.array([0.299, 0.587, 0.114], np.float32)
    tint = coloured[luma >= np.percentile(luma, 97)].mean(axis=0)
    target = 0.55 * tint + 0.45 * np.array([255, 250, 235], np.float32)
    rgb = strip[:, :, :3].astype(np.float32)
    weight = np.clip((rgb.min(axis=2) - 225) / 30, 0, 1) * 0.75
    weight[strip[:, :, 3] == 0] = 0
    strip[:, :, :3] = np.clip(rgb * (1 - weight[..., None]) + target * weight[..., None], 0, 255).astype(np.uint8)
    return strip


def _rebuild(job):
    name, png, geo, factor, warm, scaler = job
    try:
        img = decode(png)
        strip = upscale_strip(img, geo[1], factor, scaler, leaves=bool(LEAVES.search(name)))
        if warm and img[:, :, 3].min() < 255:
            strip = warm_white(strip, img)
        return name, encode(strip), None
    except Exception as e:  # one bad texture must not stop the pack
        return name, None, f"{type(e).__name__}: {e}"


class BasePacks:
    def __init__(self, paths, vanilla_only: bool, modern_names: bool) -> None:
        self.packs = [read_zip(p) for p in paths]
        self.vanilla_only = vanilla_only
        self.modern_names = modern_names

    def lookup(self, name: str) -> tuple[bytes | None, bytes | None]:
        if self.vanilla_only and not name.startswith("assets/minecraft/"):
            return None, None
        alt = name
        if self.modern_names:
            m = re.match(r"assets/minecraft/textures/(block/[a-z0-9_]+)\.png$", name)
            if m and m.group(1) in MODERN_TO_LEGACY:
                alt = f"assets/minecraft/textures/{MODERN_TO_LEGACY[m.group(1)]}.png"
        for pack in self.packs:
            if alt in pack:
                return pack[alt], pack.get(alt + ".mcmeta")
        return None, None


def plan(files: dict[str, bytes], sources: SourceIndex, bases: BasePacks, target: int, only=None, warm=True):
    """Decide what to rebuild. Returns (jobs without scaler, per-texture choice, counters)."""
    jobs, chosen = [], {}
    stats = {"base pack": 0, "original": 0, "already wide enough": 0, "pack art kept": 0, "odd layout": 0}
    for name in sorted(files):
        if not (name.endswith(".png") and name.startswith("assets/") and "/textures/" in name):
            continue
        if "/font/" in name or "/mipmaps/" in name or (only and not only.search(name)):
            continue
        orig_png, orig_meta = sources.read(name), sources.read(name + ".mcmeta")
        pack_meta = files.get(name + ".mcmeta")
        if animation(pack_meta) is None and animation(orig_meta) is None:
            continue
        fire = is_fire(name)
        base_png, base_meta = bases.lookup(name)
        orig_geo = geometry(orig_png, orig_meta) if orig_png else None
        base_geo = geometry(base_png, base_meta or orig_meta) if base_png else None
        if (base_geo and (orig_geo is None or base_geo[2] == orig_geo[2]) and base_geo[0] >= (orig_geo[0] if orig_geo else 0)
                and (name.startswith("assets/minecraft/") or same_art(base_png, base_geo, orig_png, orig_geo, fire))):
            art, meta, kind, geo = base_png, base_meta or orig_meta or pack_meta, "base pack", base_geo
        elif orig_geo:
            if not is_upscale_of(files[name], orig_png, orig_geo):
                stats["pack art kept"] += 1
                continue
            art, meta, kind, geo = orig_png, orig_meta or pack_meta, "original", orig_geo
        else:
            stats["odd layout"] += 1
            continue
        if geo[0] >= target:
            stats["already wide enough"] += 1
            continue
        factor = max(2, math.ceil(target / geo[0]))
        stats[kind] += 1
        chosen[name] = (kind, geo, meta)
        jobs.append((name, art, geo, factor, fire and warm))
    return jobs, chosen, stats


def preview_sheet(path: Path, before: dict[str, bytes], after: dict[str, bytes], names: list[str]) -> None:
    from PIL import Image

    cell = 160
    sheet = Image.new("RGBA", (cell * 6 + 12, (cell + 6) * max(1, len(names))), (22, 20, 28, 255))
    for row, name in enumerate(names):
        for col, data in enumerate((before[name], after[name])):
            img = decode(data)
            w = img.shape[1]
            count = max(1, img.shape[0] // w)
            for k, frame in enumerate((0, count // 3, 2 * count // 3)):
                tile = Image.fromarray(img[frame * w:(frame + 1) * w]).resize((cell, cell), Image.NEAREST)
                sheet.alpha_composite(tile, ((col * 3 + k) * cell + col * 12, row * (cell + 6)))
    sheet.save(path)


def run(args) -> int:
    t0 = time.time()
    scaler = get_scaler(args.scaler)
    sources = SourceIndex.build(args.vanilla, args.mods)
    bases = BasePacks(args.base, args.base_vanilla_only, args.modern_names)
    files = read_zip(args.pack)
    only = re.compile(args.only) if args.only else None
    jobs, chosen, stats = plan(files, sources, bases, args.target, only, warm=not args.no_warm)
    print(f"{args.pack.name}: {len(jobs)} animated textures to rebuild with {scaler.name}; {stats}")
    if args.dry_run:
        for name, (kind, geo, _) in list(chosen.items())[:50]:
            print(f"  {kind:10} {geo[0]}px x {geo[2]} frames  {name}")
        return 0

    out, errors = {}, []
    with Pool(args.jobs) as pool:
        for name, png, err in pool.imap_unordered(_rebuild, [j + (scaler,) for j in jobs], chunksize=4):
            if err:
                errors.append((name, err))
            else:
                out[name] = png
    for name, err in errors[:20]:
        print(f"  error {name}: {err}")
    if args.preview:
        preview_sheet(args.preview, files, out, sorted(out)[:10])
        print(f"preview: {args.preview} (left: before, right: after)")
    for name, png in out.items():
        files[name] = png
        meta = chosen[name][2]
        if meta is not None:
            files[name + ".mcmeta"] = meta
    write_zip(args.pack, files)
    print(f"rebuilt {len(out)}, errors {len(errors)}, {time.time() - t0:.0f}s")
    return 1 if errors and not out else 0
