"""NBT (Named Binary Tag) that round-trips byte for byte, and Anvil region files (``.mca``).

Values keep their on-disk form so that reading and writing an unchanged file gives identical bytes: floats and doubles
stay as their raw 4/8 big-endian bytes, and names and strings stay as raw (modified UTF-8) bytes. Integers are Python
ints. A compound is an ordered :class:`Compound`; a list is a :class:`ListTag`; arrays are lists of ints (byte arrays
are ``bytes``).

    root = nbt.loads(data)                 # Root(name, type, value)
    player = root.value                    # Compound
    player.get("XpLevel")                  # -> int
    player.set("XpLevel", nbt.INT, 30)
    assert nbt.dumps(nbt.loads(data)) == data
"""

from __future__ import annotations

import gzip
import io
import os
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

END, BYTE, SHORT, INT, LONG, FLOAT, DOUBLE, BYTE_ARRAY, STRING, LIST, COMPOUND, INT_ARRAY, LONG_ARRAY = range(13)


def _key(name: str | bytes) -> bytes:
    return name if isinstance(name, bytes) else name.encode("utf-8")


@dataclass
class ListTag:
    elem_type: int
    items: list = field(default_factory=list)


class Compound:
    """Ordered NBT compound. ``entries`` is a list of ``[name: bytes, type: int, value]``."""

    __slots__ = ("entries",)

    def __init__(self, entries: list | None = None) -> None:
        self.entries = entries if entries is not None else []

    def entry(self, name: str | bytes) -> list | None:
        key = _key(name)
        for e in self.entries:
            if e[0] == key:
                return e
        return None

    def get(self, name: str | bytes, default: Any = None) -> Any:
        e = self.entry(name)
        return e[2] if e else default

    def type_of(self, name: str | bytes) -> int | None:
        e = self.entry(name)
        return e[1] if e else None

    def set(self, name: str | bytes, tag_type: int, value: Any) -> None:
        e = self.entry(name)
        if e:
            e[1], e[2] = tag_type, value
        else:
            self.entries.append([_key(name), tag_type, value])

    def remove(self, name: str | bytes) -> None:
        key = _key(name)
        self.entries = [e for e in self.entries if e[0] != key]

    def __contains__(self, name: str | bytes) -> bool:
        return self.entry(name) is not None

    def __iter__(self) -> Iterator[list]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Compound) and self.entries == other.entries

    def __repr__(self) -> str:
        return "Compound(" + ", ".join(e[0].decode("utf-8", "replace") for e in self.entries) + ")"


@dataclass
class Root:
    name: bytes
    type: int
    value: Any


# --------------------------------------------------------------------------------------------- reading


class _Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        b = self.data[self.pos:self.pos + n]
        if len(b) != n:
            raise ValueError("truncated NBT")
        self.pos += n
        return b

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        v = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return v[0] if len(v) == 1 else v

    def string(self) -> bytes:
        return self.take(self.unpack(">H"))

    def payload(self, t: int):
        if t == BYTE:
            return self.unpack(">b")
        if t == SHORT:
            return self.unpack(">h")
        if t == INT:
            return self.unpack(">i")
        if t == LONG:
            return self.unpack(">q")
        if t == FLOAT:
            return self.take(4)
        if t == DOUBLE:
            return self.take(8)
        if t == BYTE_ARRAY:
            return self.take(self.unpack(">i"))
        if t == STRING:
            return self.string()
        if t == LIST:
            et = self.take(1)[0]
            n = self.unpack(">i")
            return ListTag(et, [self.payload(et) for _ in range(max(n, 0))])
        if t == COMPOUND:
            entries = []
            while True:
                tt = self.take(1)[0]
                if tt == END:
                    return Compound(entries)
                entries.append([self.string(), tt, self.payload(tt)])
        if t == INT_ARRAY:
            n = self.unpack(">i")
            return list(struct.unpack(f">{n}i", self.take(4 * n)))
        if t == LONG_ARRAY:
            n = self.unpack(">i")
            return list(struct.unpack(f">{n}q", self.take(8 * n)))
        raise ValueError(f"unknown NBT tag type {t}")


def loads(data: bytes) -> Root:
    r = _Reader(data)
    t = r.take(1)[0]
    return Root(r.string(), t, r.payload(t))


# --------------------------------------------------------------------------------------------- writing


def _write(out: io.BytesIO, t: int, v) -> None:
    if t == BYTE:
        out.write(struct.pack(">b", v))
    elif t == SHORT:
        out.write(struct.pack(">h", v))
    elif t == INT:
        out.write(struct.pack(">i", v))
    elif t == LONG:
        out.write(struct.pack(">q", v))
    elif t in (FLOAT, DOUBLE):
        out.write(v)
    elif t == BYTE_ARRAY:
        out.write(struct.pack(">i", len(v)))
        out.write(v)
    elif t == STRING:
        out.write(struct.pack(">H", len(v)))
        out.write(v)
    elif t == LIST:
        out.write(bytes([v.elem_type]))
        out.write(struct.pack(">i", len(v.items)))
        for item in v.items:
            _write(out, v.elem_type, item)
    elif t == COMPOUND:
        for name, tt, value in v.entries:
            out.write(bytes([tt]))
            out.write(struct.pack(">H", len(name)))
            out.write(name)
            _write(out, tt, value)
        out.write(b"\x00")
    elif t == INT_ARRAY:
        out.write(struct.pack(f">i{len(v)}i", len(v), *v))
    elif t == LONG_ARRAY:
        out.write(struct.pack(f">i{len(v)}q", len(v), *v))
    else:
        raise ValueError(f"unknown NBT tag type {t}")


def dumps(root: Root) -> bytes:
    out = io.BytesIO()
    out.write(bytes([root.type]))
    out.write(struct.pack(">H", len(root.name)))
    out.write(root.name)
    _write(out, root.type, root.value)
    return out.getvalue()


# --------------------------------------------------------------------------------------------- files


def read_file(path: str | os.PathLike) -> tuple[Root, bool]:
    """(root, gzipped) of an NBT file such as level.dat, playerdata/<uuid>.dat or data/<name>.dat.

    Raises ValueError if the file would not be written back byte for byte (so no edit can silently drop data).
    """
    raw = Path(path).read_bytes()
    gz = raw[:2] == b"\x1f\x8b"
    data = gzip.decompress(raw) if gz else raw
    root = loads(data)
    if dumps(root) != data:
        raise ValueError(f"{path}: NBT does not round-trip exactly; refusing to edit it")
    return root, gz


def encode_file(root: Root, gzipped: bool) -> bytes:
    """The file bytes for ``root``, gzipped like the original (the caller decides where, and with which backup)."""
    data = dumps(root)
    return gzip.compress(data) if gzipped else data


# --------------------------------------------------------------------------------------------- region files


class UnsupportedChunk(Exception):
    pass


class RegionFile:
    """An Anvil region file (32x32 chunks). Chunks are read and written as raw NBT bytes.

    Supports gzip (1), zlib (2) and uncompressed (3) chunks; LZ4 (4) and oversized chunks stored in separate
    ``.mcc`` files raise :class:`UnsupportedChunk` and are left untouched.
    """

    SECTOR = 4096

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self.buf = bytearray(self.path.read_bytes())
        self.dirty = False

    def slots(self) -> Iterator[int]:
        """Indexes (0..1023) of the chunks present in the file."""
        if len(self.buf) < 2 * self.SECTOR:
            return
        for i in range(1024):
            offset = int.from_bytes(self.buf[i * 4:i * 4 + 3], "big")
            if offset and self.buf[i * 4 + 3]:
                yield i

    def _location(self, i: int) -> tuple[int, int]:
        return int.from_bytes(self.buf[i * 4:i * 4 + 3], "big") * self.SECTOR, self.buf[i * 4 + 3]

    def read(self, i: int) -> tuple[bytes, int]:
        """(decompressed chunk NBT, compression scheme)."""
        start, _ = self._location(i)
        length = struct.unpack(">i", self.buf[start:start + 4])[0]
        scheme = self.buf[start + 4]
        body = bytes(self.buf[start + 5:start + 4 + length])
        if scheme & 0x80:
            raise UnsupportedChunk(f"chunk {i} is stored in an external .mcc file")
        if scheme == 1:
            return gzip.decompress(body), scheme
        if scheme == 2:
            return zlib.decompress(body), scheme
        if scheme == 3:
            return body, scheme
        raise UnsupportedChunk(f"chunk {i} uses compression scheme {scheme}")

    def write(self, i: int, data: bytes, scheme: int = 2) -> None:
        """Replace chunk ``i``; reuses its sectors when the new data fits, else appends it at the end of the file."""
        body = gzip.compress(data) if scheme == 1 else zlib.compress(data) if scheme == 2 else data
        blob = struct.pack(">i", len(body) + 1) + bytes([scheme]) + body
        need = -(-len(blob) // self.SECTOR)
        if need > 255:
            raise UnsupportedChunk(f"chunk {i} would need {need} sectors (more than 255)")
        start, count = self._location(i)
        if need <= count:
            self.buf[start:start + count * self.SECTOR] = blob.ljust(count * self.SECTOR, b"\x00")
        else:
            end = len(self.buf) + (-len(self.buf)) % self.SECTOR
            self.buf.extend(b"\x00" * (end - len(self.buf)))
            self.buf.extend(blob.ljust(need * self.SECTOR, b"\x00"))
            self.buf[i * 4:i * 4 + 4] = (end // self.SECTOR).to_bytes(3, "big") + bytes([need])
        self.dirty = True

    def to_bytes(self) -> bytes:
        return bytes(self.buf)
