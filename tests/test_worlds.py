import argparse
import gzip
import zipfile

from mcpacktools import nbt
from mcpacktools.nbt import Root
from mcpacktools.world import finder
from helpers import compound, write_nbt


def level(name: str, seed: int, played: int):
    return compound(Data=(nbt.COMPOUND, compound(LevelName=(nbt.STRING, name.encode()), RandomSeed=(nbt.LONG, seed),
                                                 LastPlayed=(nbt.LONG, played))))


def test_scan_folders_and_zips(tmp_path):
    old = tmp_path / "old"
    write_nbt(old / "saves" / "Castle" / "level.dat", level("Castle", 42, 2_000_000))
    write_nbt(old / "saves" / "Farm" / "level.dat", level("Farm", 7, 1_000_000))
    with zipfile.ZipFile(old / "backup.zip", "w") as z:
        z.writestr("Castle/level.dat", gzip.compress(nbt.dumps(Root(b"", nbt.COMPOUND, level("Castle", 42, 1)))))
    new = tmp_path / "new"
    write_nbt(new / "Castle" / "level.dat", level("Castle", 42, 1_000_000))

    copies = finder.scan([old])
    assert len(copies) == 3
    groups = finder.newest(copies)
    assert groups[(42, "castle")].last_played == 2_000_000

    args = argparse.Namespace(roots=[old], against=[new], no_zips=False)
    assert finder.run(args) == 0


def test_modern_seed_location(tmp_path):
    settings = compound(seed=(nbt.LONG, 99))
    data = compound(Data=(nbt.COMPOUND, compound(LevelName=(nbt.STRING, b"New"), WorldGenSettings=(nbt.COMPOUND, settings),
                                                 LastPlayed=(nbt.LONG, 5))))
    write_nbt(tmp_path / "New" / "level.dat", data)
    (copy,) = finder.scan([tmp_path])
    assert (copy.name, copy.seed) == ("New", 99)
