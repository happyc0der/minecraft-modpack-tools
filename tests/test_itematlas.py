import argparse
import json

from mcpacktools import itematlas
from mcpacktools.packfiles import read_zip
from helpers import make_zip, png, solid


def client_jar(path):
    return make_zip(path, {
        "version.json": {"id": "26.2", "pack_version": {"resource_major": 88, "data_major": 107}},
        "assets/minecraft/atlases/items.json": {"sources": [{"type": "directory", "source": "item", "prefix": "item/"}]},
        "assets/minecraft/atlases/blocks.json": {"sources": [{"type": "directory", "source": "block", "prefix": "block/"}]},
        "assets/minecraft/models/item/generated.json": {"parent": "builtin/generated"},
        "assets/minecraft/textures/block/stone.png": png(solid(16, 16)),
    })


def gem_pack(path):
    """An item whose two layers come from different atlases: purple/black in 26.x."""
    return make_zip(path, {
        "pack.mcmeta": {"pack": {"pack_format": 88, "description": "t"}},
        "assets/mymod/items/gem.json": {"model": {"type": "minecraft:model", "model": "mymod:item/gem"}},
        "assets/mymod/models/item/gem.json": {"parent": "minecraft:item/generated",
                                              "textures": {"layer0": "mymod:item/gem", "layer1": "minecraft:block/stone"}},
        "assets/mymod/textures/item/gem.png": png(solid(16, 16)),
        "assets/mymod/items/rune.json": {"model": {"type": "minecraft:model", "model": "mymod:item/rune"}},
        "assets/mymod/models/item/rune.json": {"parent": "minecraft:item/generated",
                                               "textures": {"layer0": "mymod:misc/rune"}},
        "assets/mymod/textures/misc/rune.png": png(solid(16, 16)),
    })


def ns(pack, out=None, client=None, check=False):
    return argparse.Namespace(pack=pack, out=out, client=client, pack_format=None, copy_namespace="atlasfix",
                              check=check, list=False)


def test_check_reports_mixed_atlases(tmp_path, capsys):
    client = client_jar(tmp_path / "client.jar")
    pack = gem_pack(tmp_path / "pack.zip")
    assert itematlas.run(ns(pack, client=client, check=True)) == 1
    assert "items failing to bake: 1" in capsys.readouterr().out


def test_fix_copies_and_registers_textures(tmp_path, capsys):
    client = client_jar(tmp_path / "client.jar")
    pack = gem_pack(tmp_path / "pack.zip")
    out = tmp_path / "fixed.zip"
    assert itematlas.run(ns(pack, out=out, client=client)) == 0
    files = read_zip(out)
    gem = json.loads(files["assets/mymod/models/item/gem.json"])
    assert gem["textures"]["layer1"] == "atlasfix:item/minecraft/block/stone"
    assert "assets/atlasfix/textures/item/minecraft/block/stone.png" in files
    items_atlas = json.loads(files["assets/minecraft/atlases/items.json"])
    assert {"type": "minecraft:single", "resource": "mymod:misc/rune"} in items_atlas["sources"]
    report = capsys.readouterr().out
    assert "[after] items failing to bake: 0 | item models with missing textures: 0" in report
