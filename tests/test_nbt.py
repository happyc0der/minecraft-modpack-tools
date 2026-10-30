import struct
import zlib

from mcpacktools import nbt
from mcpacktools.nbt import Compound, ListTag, RegionFile, Root


def sample() -> Root:
    inner = Compound([[b"x", nbt.INT, -5], [b"f", nbt.FLOAT, struct.pack(">f", 1.5)]])
    value = Compound([
        [b"b", nbt.BYTE, -1], [b"s", nbt.SHORT, 300], [b"i", nbt.INT, 2 ** 31 - 1], [b"l", nbt.LONG, -(2 ** 63)],
        [b"d", nbt.DOUBLE, struct.pack(">d", 0.1)], [b"ba", nbt.BYTE_ARRAY, b"\x00\xff"],
        [b"str", nbt.STRING, "héllo".encode()], [b"list", nbt.LIST, ListTag(nbt.COMPOUND, [inner])],
        [b"empty", nbt.LIST, ListTag(nbt.END, [])], [b"ia", nbt.INT_ARRAY, [1, -2, 3, 4]],
        [b"la", nbt.LONG_ARRAY, [2 ** 62, -1]], [b"c", nbt.COMPOUND, Compound()],
    ])
    return Root(b"root", nbt.COMPOUND, value)


def test_round_trip_is_byte_exact():
    data = nbt.dumps(sample())
    assert nbt.dumps(nbt.loads(data)) == data


def test_compound_access():
    c = nbt.loads(nbt.dumps(sample())).value
    assert c.get("s") == 300 and c.type_of("s") == nbt.SHORT
    c.set("s", nbt.SHORT, 7)
    c.set("new", nbt.STRING, b"v")
    assert c.get("s") == 7 and "new" in c
    c.remove("new")
    assert "new" not in c
    assert c.get("list").items[0].get("x") == -5


def _region_with(chunks: dict[int, bytes]) -> bytes:
    """A region file with each chunk zlib-compressed in its own sector run."""
    header = bytearray(8192)
    body = bytearray()
    sector = 2
    for i, data in chunks.items():
        comp = zlib.compress(data)
        blob = struct.pack(">i", len(comp) + 1) + b"\x02" + comp
        n = -(-len(blob) // 4096)
        header[i * 4:i * 4 + 4] = sector.to_bytes(3, "big") + bytes([n])
        body += blob.ljust(n * 4096, b"\x00")
        sector += n
    return bytes(header + body)


def test_region_read_and_rewrite(tmp_path):
    small = nbt.dumps(Root(b"", nbt.COMPOUND, Compound([[b"v", nbt.INT, 1]])))
    path = tmp_path / "r.0.0.mca"
    path.write_bytes(_region_with({0: small, 5: small}))
    region = RegionFile(path)
    assert list(region.slots()) == [0, 5]
    data, scheme = region.read(5)
    assert data == small and scheme == 2

    big = nbt.dumps(Root(b"", nbt.COMPOUND, Compound([[b"blob", nbt.BYTE_ARRAY, bytes(range(256)) * 40]])))
    region.write(0, big, scheme=3)  # uncompressed and larger than its sector: appended at the end
    assert region.read(0) == (big, 3)
    assert region.read(5)[0] == small
    path.write_bytes(region.to_bytes())
    assert RegionFile(path).read(0)[0] == big
