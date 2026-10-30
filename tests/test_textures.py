import argparse
import json

import numpy as np

from mcpacktools.packfiles import png_size, read_zip
from mcpacktools.scaling import decode, nearest
from mcpacktools.textures import animated, atlasfit, upscale
from helpers import make_zip, png, solid

ANIM = json.dumps({"animation": {"frametime": 2}})


def fire_strip() -> np.ndarray:
    """Two 16x16 cut-out frames, orange with a white core."""
    strip = np.zeros((32, 16, 4), np.uint8)
    for f in range(2):
        strip[f * 16 + 4:f * 16 + 14, 3:13] = (230, 120, 20, 255)
        strip[f * 16 + 8:f * 16 + 12, 6:10] = (255, 255, 255, 255)
    return strip


def args(**kw):
    defaults = dict(mods=None, base=[], base_vanilla_only=False, modern_names=False, target=64, scaler="scale2x",
                    only=None, no_warm=False, preview=None, dry_run=False, jobs=1)
    return argparse.Namespace(**{**defaults, **kw})


def test_animated_rebuilds_strip_frame_by_frame(tmp_path):
    fire = fire_strip()
    vanilla = make_zip(tmp_path / "client.jar", {
        "assets/minecraft/textures/block/fire_0.png": png(fire),
        "assets/minecraft/textures/block/fire_0.png.mcmeta": ANIM})
    # the pack holds a plain one-image upscale of the strip, the kind of damage this command repairs
    pack = make_zip(tmp_path / "pack.zip", {"assets/minecraft/textures/block/fire_0.png": png(nearest(fire, 4)),
                                            "pack.mcmeta": "{}"})
    assert animated.run(args(pack=pack, vanilla=vanilla)) == 0
    files = read_zip(pack)
    out = decode(files["assets/minecraft/textures/block/fire_0.png"])
    assert out.shape == (128, 64, 4)
    assert set(np.unique(out[:, :, 3])) <= {0, 255}
    assert files["assets/minecraft/textures/block/fire_0.png.mcmeta"] == ANIM.encode()
    assert out[:, :, :3][out[:, :, 3] > 0].min(axis=1).max() < 255  # the white core is tinted, not flat white


def test_animated_prefers_base_pack_frames(tmp_path):
    fire = fire_strip()
    vanilla = make_zip(tmp_path / "client.jar", {
        "assets/minecraft/textures/block/fire_0.png": png(fire),
        "assets/minecraft/textures/block/fire_0.png.mcmeta": ANIM})
    base_art = nearest(fire, 2)
    base_art[base_art[:, :, 3] > 0] = (200, 60, 10, 255)  # hand-drawn, darker
    base = make_zip(tmp_path / "base32.zip", {"assets/minecraft/textures/block/fire_0.png": png(base_art),
                                             "assets/minecraft/textures/block/fire_0.png.mcmeta": ANIM})
    pack = make_zip(tmp_path / "pack.zip", {"assets/minecraft/textures/block/fire_0.png": png(nearest(fire, 4))})
    animated.run(args(pack=pack, vanilla=vanilla, base=[base], no_warm=True))
    out = decode(read_zip(pack)["assets/minecraft/textures/block/fire_0.png"])
    assert tuple(out[out[:, :, 3] > 0][0][:3]) == (200, 60, 10)


def test_animated_keeps_hand_made_art(tmp_path):
    fire = fire_strip()
    vanilla = make_zip(tmp_path / "client.jar", {
        "assets/minecraft/textures/block/fire_0.png": png(fire),
        "assets/minecraft/textures/block/fire_0.png.mcmeta": ANIM})
    other = png(solid(128, 64, (1, 2, 3, 255)))
    pack = make_zip(tmp_path / "pack.zip", {"assets/minecraft/textures/block/fire_0.png": other})
    animated.run(args(pack=pack, vanilla=vanilla))
    assert read_zip(pack)["assets/minecraft/textures/block/fire_0.png"] == other


def test_upscale_fill_pack(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    make_zip(mods / "mymod.jar", {
        "assets/mymod/textures/block/ore.png": png(solid(16, 16)),
        "assets/mymod/textures/block/goo.png": png(np.concatenate([solid(16, 16), solid(16, 16, (0, 0, 255, 255))])),
        "assets/mymod/textures/block/goo.png.mcmeta": ANIM,
        "assets/mymod/textures/gui/huge.png": png(solid(512, 512)),
    })
    hd = make_zip(tmp_path / "hd.zip", {"assets/mymod/textures/block/ore.png": png(solid(32, 32))})
    out = tmp_path / "fill.zip"
    ns = argparse.Namespace(mods=mods, vanilla=None, skip=[hd], merge_into=None, out=out, factor=4, max_size=256,
                            legacy=False, pack_format=15, description="test", scaler="scale2x", dry_run=False, jobs=1)
    assert upscale.run(ns) == 0
    files = read_zip(out)
    assert "assets/mymod/textures/block/ore.png" not in files            # the HD pack has it
    assert "assets/mymod/textures/gui/huge.png" not in files             # already high-res
    goo = decode(files["assets/mymod/textures/block/goo.png"])
    assert goo.shape == (128, 64, 4) and (goo[64:] == (0, 0, 255, 255)).all()
    assert "assets/mymod/textures/block/goo.png.mcmeta" in files and "pack.mcmeta" in files


def test_atlas_fit_caps_sprites(tmp_path):
    cut = solid(256, 256)
    cut[:, :128, 3] = 0
    pack = make_zip(tmp_path / "pack.zip", {"assets/mymod/textures/blocks/big.png": png(cut),
                                            "assets/mymod/textures/gui/menu.png": png(solid(512, 512))})
    ns = argparse.Namespace(pack=pack, cap=128, extra=[], extra_cap=256, mipmap=4, dry_run=False, jobs=1)
    assert atlasfit.run(ns) == 0
    files = read_zip(pack)
    assert png_size(files["assets/mymod/textures/blocks/big.png"]) == (128, 128)
    assert png_size(files["assets/mymod/textures/gui/menu.png"]) == (512, 512)  # not in the atlas
    assert set(np.unique(decode(files["assets/mymod/textures/blocks/big.png"])[:, :, 3])) <= {0, 255}
