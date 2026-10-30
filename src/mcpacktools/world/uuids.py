"""Player UUIDs in every encoding Minecraft and mods write them, and a rewriter that swaps one for another.

Encodings recognised:

* two LONG tags ``<prefix>Most`` / ``<prefix>Least`` (1.15 and older, and most mods: ``UUIDMost``, ``ownerMost``...);
  also ``<prefix>U`` / ``<prefix>L`` (Extra Utilities 2);
* an INT_ARRAY of 4 ints (1.16+ ``UUID: [I; ...]``);
* a string, with or without dashes, on its own or inside a longer string (``"player:<uuid>"``);
* a compound entry or JSON key named after the UUID (per-player maps).

Lists and maps that hold one record per player can end up with two records for the target (its own, and the
renamed source). By default the source's record wins and the target's is dropped; with ``keep_target_records`` the
source's record is left as it was instead. Every change is recorded in :attr:`UuidRewriter.hits`.
"""

from __future__ import annotations

import hashlib
import re
from uuid import UUID

from mcpacktools.nbt import COMPOUND, INT_ARRAY, LIST, LONG, STRING, Compound, ListTag

NIL = UUID(int=0)
PAIR_SUFFIXES = (("Most", "Least"), ("U", "L"))
NAME_KEYS = {b"Name", b"name", b"Title", b"Owner", b"OwnerName", b"ownerName", b"PlayerName", b"playerName",
             b"Username", b"username", b"LastKnownName", b"lastKnownName"}


def parse_uuid(text: str) -> UUID:
    """A UUID with or without dashes, or ``offline:<name>`` for the UUID an offline-mode server gives that name."""
    if text.lower().startswith("offline:"):
        return offline_uuid(text.split(":", 1)[1])
    return UUID(text)


def offline_uuid(name: str) -> UUID:
    """Java's ``UUID.nameUUIDFromBytes("OfflinePlayer:" + name)``: MD5, version 3."""
    h = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
    h[6] = (h[6] & 0x0F) | 0x30
    h[8] = (h[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(h))


def to_longs(u: UUID) -> tuple[int, int]:
    """(most, least) as signed 64-bit ints."""
    signed = lambda x: x - (1 << 64) if x >= 1 << 63 else x
    return signed(u.int >> 64), signed(u.int & ((1 << 64) - 1))


def to_ints(u: UUID) -> list[int]:
    signed = lambda x: x - (1 << 32) if x >= 1 << 31 else x
    return [signed((u.int >> (96 - 32 * i)) & 0xFFFFFFFF) for i in range(4)]


def from_longs(most: int, least: int) -> UUID:
    return UUID(int=((most & ((1 << 64) - 1)) << 64) | (least & ((1 << 64) - 1)))


def search_tokens(u: UUID) -> list[bytes]:
    """Byte patterns that appear in raw NBT/JSON wherever ``u`` is stored (for a fast skip before parsing)."""
    most, least = to_longs(u)
    return [most.to_bytes(8, "big", signed=True), least.to_bytes(8, "big", signed=True),
            str(u).encode(), u.hex.encode()]


class UuidRewriter:
    def __init__(self, src: UUID, dst: UUID, *, src_name: str | None = None, dst_name: str | None = None,
                 keep_target_records: bool = False, only_ids: re.Pattern | None = None) -> None:
        self.src, self.dst = src, dst
        self.src_longs, self.dst_longs = to_longs(src), to_longs(dst)
        self.src_ints, self.dst_ints = to_ints(src), to_ints(dst)
        self.names = (src_name.encode(), dst_name.encode()) if src_name and dst_name else None
        self.keep_target_records = keep_target_records
        self.only_ids = only_ids
        self.hits: list[str] = []    # changes made
        self.notes: list[str] = []   # records deliberately left alone

    # ------------------------------------------------------------------ strings
    def _string(self, s: str) -> str:
        low = s.lower()
        if low == self.src.hex:
            return self.dst.hex
        return re.sub(re.escape(str(self.src)), str(self.dst), s, flags=re.IGNORECASE)

    def _string_is(self, value: bytes, u: UUID) -> bool:
        low = value.decode("utf-8", "replace").lower()
        return low in (str(u), u.hex)

    # ------------------------------------------------------------------ identity of a record
    def holds(self, c: Compound, u: UUID) -> bool:
        """Does this compound itself (not its children) store ``u``?"""
        longs, ints = to_longs(u), to_ints(u)
        for most, least in self._pairs(c):
            if (most[2], least[2]) == longs:
                return True
        for name, t, v in c.entries:
            if (t == INT_ARRAY and v == ints) or (t == STRING and self._string_is(v, u)):
                return True
        return False

    @staticmethod
    def _pairs(c: Compound):
        longs = {e[0]: e for e in c.entries if e[1] == LONG}
        for name, entry in longs.items():
            for most, least in PAIR_SUFFIXES:
                m, l = most.encode(), least.encode()
                if name.endswith(m) and name[:-len(m)] + l in longs:
                    yield entry, longs[name[:-len(m)] + l]

    # ------------------------------------------------------------------ walking
    def compound(self, c: Compound, where: str, allowed: bool = True) -> int:
        """Rewrite inside ``c``; returns how many of ``c``'s own fields changed."""
        if self.only_ids is not None and not allowed:
            ident = c.get("id")
            allowed = isinstance(ident, bytes) and bool(self.only_ids.search(ident.decode("utf-8", "replace")))
        direct = 0
        if allowed:
            for most, least in list(self._pairs(c)):
                if (most[2], least[2]) == self.src_longs:
                    most[2], least[2] = self.dst_longs
                    direct += 1
                    self.hits.append(f"{where}/{most[0].decode()}+{least[0].decode()}")
            direct += self._map_keys(c, where)
        for entry in c.entries:
            name, t, v = entry
            here = f"{where}/{name.decode('utf-8', 'replace')}"
            if t == INT_ARRAY and allowed and v == self.src_ints:
                entry[2] = list(self.dst_ints)
                direct += 1
                self.hits.append(here)
            elif t == STRING and allowed:
                new = self._string(v.decode("utf-8", "surrogateescape")).encode("utf-8", "surrogateescape")
                if new != v:
                    entry[2] = new
                    direct += 1
                    self.hits.append(here)
            elif t == COMPOUND:
                self.compound(v, here, allowed)
            elif t == LIST:
                self.list(v, here, allowed)
        if direct and allowed and self.names:
            for entry in c.entries:
                if entry[1] == STRING and entry[0] in NAME_KEYS and entry[2] == self.names[0]:
                    entry[2] = self.names[1]
                    self.hits.append(f"{where}/{entry[0].decode()} (name)")
        return direct

    def _map_keys(self, c: Compound, where: str) -> int:
        """Compound entries named after the UUID (per-player maps)."""
        src_keys = [e for e in c.entries if self._string_is(e[0], self.src)]
        if not src_keys:
            return 0
        dst_keys = [e for e in c.entries if self._string_is(e[0], self.dst)]
        if dst_keys:
            if self.keep_target_records:
                self.notes.append(f"{where}: the target already has a record; kept it, left the source's as it was")
                return 0
            c.entries = [e for e in c.entries if e not in dst_keys]
            self.hits.append(f"{where}: dropped the target's own record")
        for e in src_keys:
            e[0] = self._string(e[0].decode()).encode()
            self.hits.append(f"{where}/{e[0].decode()} (key)")
        return len(src_keys)

    def list(self, lst: ListTag, where: str, allowed: bool = True) -> None:
        items = lst.items
        skip = set()
        if lst.elem_type == COMPOUND and allowed:
            src_idx = [i for i, c in enumerate(items) if self.holds(c, self.src)]
            dst_idx = [i for i, c in enumerate(items) if self.holds(c, self.dst)]
            if src_idx and dst_idx:
                if self.keep_target_records:
                    skip = set(src_idx)
                    self.notes.append(f"{where}: the target already has a record; kept it, left the source's as it was")
                else:
                    lst.items = items = [c for i, c in enumerate(items) if i not in set(dst_idx)]
                    self.hits.append(f"{where}: dropped the target's own record")
        for i, v in enumerate(items):
            here = f"{where}[{i}]"
            if lst.elem_type == COMPOUND and i not in skip:
                self.compound(v, here, allowed)
            elif lst.elem_type == LIST:
                self.list(v, here, allowed)
            elif lst.elem_type == INT_ARRAY and allowed and v == self.src_ints:
                items[i] = list(self.dst_ints)
                self.hits.append(here)
            elif lst.elem_type == STRING and allowed:
                new = self._string(v.decode("utf-8", "surrogateescape")).encode("utf-8", "surrogateescape")
                if new != v:
                    items[i] = new
                    self.hits.append(here)

    def json(self, node, where: str):
        """Rewrite a parsed JSON value; returns the new value."""
        if isinstance(node, dict):
            src =[k for k in node if k.lower() in (str(self.src), self.src.hex)]
            if src and any(k.lower() in (str(self.dst), self.dst.hex) for k in node):
                if self.keep_target_records:
                    self.notes.append(f"{where}: the target already has a record; kept it, left the source's as it was")
                    src = []
                else:
                    node = {k: v for k, v in node.items() if k.lower() not in (str(self.dst), self.dst.hex)}
                    self.hits.append(f"{where}: dropped the target's own record")
            out = {}
            for k, v in node.items():
                nk = self._string(k) if k in src else k
                if nk != k:
                    self.hits.append(f"{where}/{nk} (key)")
                out[nk] = self.json(v, f"{where}/{nk}")
            return out
        if isinstance(node, list):
            return [self.json(v, f"{where}[{i}]") for i, v in enumerate(node)]
        if isinstance(node, str):
            new = self._string(node)
            if new != node:
                self.hits.append(where)
            return new
        return node
