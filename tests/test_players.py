import argparse
import json
import zlib
import struct
from uuid import UUID

import pytest

from mcpacktools import nbt
from mcpacktools.nbt import Compound, ListTag, RegionFile, Root
from mcpacktools.world import players
from mcpacktools.world.uuids import NIL, offline_uuid, to_ints, to_longs
from helpers import compound, inventory, item, read_nbt, write_nbt

SRC = UUID("11111111-2222-3333-4444-555555555555")
DST = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def player(u: UUID, *items) -> Compound:
    most, least = to_longs(u)
    return compound(UUIDMost=(nbt.LONG, most), UUIDLeast=(nbt.LONG, least), XpLevel=(nbt.INT, 1),
                    Inventory=(nbt.LIST, inventory(*items)))


def owned(u: UUID, ident: str) -> Compound:
    most, least = to_longs(u)
    return compound(id=(nbt.STRING, ident.encode()), ownerMost=(nbt.LONG, most), ownerLeast=(nbt.LONG, least))


def region_bytes(chunk: Compound) -> bytes:
    data = zlib.compress(nbt.dumps(Root(b"", nbt.COMPOUND, chunk)))
    blob = struct.pack(">i", len(data) + 1) + b"\x02" + data
    header = bytearray(8192)
    header[0:4] = (2).to_bytes(3, "big") + bytes([-(-len(blob) // 4096)])
    return bytes(header) + blob.ljust(-(-len(blob) // 4096) * 4096, b"\x00")


@pytest.fixture
def world(tmp_path):
    w = tmp_path / "world"
    write_nbt(w / "playerdata" / f"{SRC}.dat", player(SRC, item(0, "minecraft:diamond")))
    write_nbt(w / "playerdata" / f"{DST}.dat", player(DST, item(0, "minecraft:dirt")))
    host = player(DST, item(0, "minecraft:dirt"))
    write_nbt(w / "level.dat", compound(Data=(nbt.COMPOUND, compound(Player=(nbt.COMPOUND, host),
                                                                     LevelName=(nbt.STRING, b"w")))))
    (w / "playerdata" / f"{SRC}.baub").write_bytes(b"mod player data")
    (w / "stats").mkdir()
    (w / "stats" / f"{SRC}.json").write_text(json.dumps({"stat.jump": 5, "DataVersion": 1}))
    (w / "stats" / f"{DST}.json").write_text(json.dumps({"stat.jump": 2, "DataVersion": 1}))
    records = ListTag(nbt.COMPOUND, [
        compound(UUID=(nbt.INT_ARRAY, to_ints(SRC)), Data=(nbt.STRING, b"source's")),
        compound(UUID=(nbt.INT_ARRAY, to_ints(DST)), Data=(nbt.STRING, b"target's")),
    ])
    write_nbt(w / "data" / "modteams.dat", compound(data=(nbt.COMPOUND, compound(Players=(nbt.LIST, records)))))
    (w / "data" / "profiles.json").write_text(json.dumps({"profiles": {str(SRC): {"lvl": 3}}}))
    level = compound(TileEntities=(nbt.LIST, ListTag(nbt.COMPOUND, [owned(SRC, "mymod:machine")])))
    (w / "region").mkdir()
    (w / "region" / "r.0.0.mca").write_bytes(region_bytes(compound(Level=(nbt.COMPOUND, level))))
    return w


def ns(world, **kw):
    defaults = dict(src=str(SRC), dst=str(DST), from_name=None, to_name=None, scope="player,data,regions", host=False,
                    replace_target_items=False, keep_target_records=False, only_ids=None, allow_nil=False,
                    backup_dir=world.parent / "backup", dry_run=False)
    return argparse.Namespace(world=world, **{**defaults, **kw})


def test_transfer_moves_everything(world):
    assert players.run(ns(world)) == 0
    new = read_nbt(world / "playerdata" / f"{DST}.dat")
    assert (new.get("UUIDMost"), new.get("UUIDLeast")) == to_longs(DST)
    slots = {i.get("id"): i.get("Slot") for i in new.get("Inventory").items}
    assert slots == {b"minecraft:diamond": 0, b"minecraft:dirt": 9}       # target's item kept in a free slot
    host = read_nbt(world / "level.dat").get("Data").get("Player")
    assert host == new
    assert json.loads((world / "stats" / f"{DST}.json").read_text()) == {"stat.jump": 7, "DataVersion": 1}
    assert (world / "playerdata" / f"{DST}.baub").read_bytes() == b"mod player data"
    teams = read_nbt(world / "data" / "modteams.dat").get("data").get("Players").items
    assert [(t.get("UUID"), t.get("Data")) for t in teams] == [(to_ints(DST), b"source's")]
    assert json.loads((world / "data" / "profiles.json").read_text()) == {"profiles": {str(DST): {"lvl": 3}}}
    chunk = nbt.loads(RegionFile(world / "region" / "r.0.0.mca").read(0)[0]).value
    machine = chunk.get("Level").get("TileEntities").items[0]
    assert (machine.get("ownerMost"), machine.get("ownerLeast")) == to_longs(DST)
    assert (world / "playerdata" / f"{SRC}.dat").exists()                 # source left in place
    assert (world.parent / "backup" / "level.dat").exists()


def test_dry_run_writes_nothing(world, capsys):
    before = {p: p.read_bytes() for p in world.rglob("*") if p.is_file()}
    players.run(ns(world, dry_run=True))
    assert {p: p.read_bytes() for p in world.rglob("*") if p.is_file()} == before
    assert "DRY RUN" in capsys.readouterr().out


def test_keep_target_records(world):
    teams_file = world / "data" / "modteams.dat"
    before = teams_file.read_bytes()
    players.run(ns(world, scope="data", keep_target_records=True))
    assert teams_file.read_bytes() == before                      # nothing to change there: not even rewritten
    profiles = json.loads((world / "data" / "profiles.json").read_text())
    assert profiles == {"profiles": {str(DST): {"lvl": 3}}}       # no conflict there, so it still moves


def test_name_keyed_file_goes_to_the_target_name(world):
    most, least = to_longs(SRC)
    write_nbt(world / "data" / "teams" / "players" / "olduser.dat",
              compound(UUIDMost=(nbt.LONG, most), UUIDLeast=(nbt.LONG, least), Name=(nbt.STRING, b"OldUser")))
    players.run(ns(world, scope="data", from_name="OldUser", to_name="NewUser"))
    moved = read_nbt(world / "data" / "teams" / "players" / "newuser.dat")
    assert (moved.get("UUIDMost"), moved.get("UUIDLeast"), moved.get("Name")) == (*to_longs(DST), b"NewUser")
    assert read_nbt(world / "data" / "teams" / "players" / "olduser.dat").get("Name") == b"OldUser"  # left as it was


def test_nil_uuid_needs_explicit_permission(world):
    with pytest.raises(SystemExit):
        players.run(ns(world, src=str(NIL)))


def test_offline_uuid_is_version_3():
    u = offline_uuid("Steve")
    assert u.version == 3 and u.variant == "specified in RFC 4122"
