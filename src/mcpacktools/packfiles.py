"""Resource-pack zips: reading them into memory, writing them back atomically, and finding original textures.

A resource pack is handled as a plain ``{path: bytes}`` dict. Original textures (the ones a pack replaces) come from
the game's client jar and from mod jars; :class:`SourceIndex` records which archive holds each one.
"""

from __future__ import annotations

import json
import os
import struct
import zipfile
from pathlib import Path
from typing import Callable, Iterable

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG header without decoding the image, or None if ``data`` is not a PNG."""
    if len(data) < 24 or data[:8] != PNG_MAGIC:
        return None
    return struct.unpack(">II", data[16:24])


def read_zip(path: str | os.PathLike) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {info.filename: z.read(info) for info in z.infolist() if not info.is_dir()}


def write_zip(path: str | os.PathLike, files: dict[str, bytes], level: int = 6) -> None:
    """Write ``files`` to a zip at ``path``: first to ``<path>.tmp``, then swapped in, so a crash never leaves half a pack."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=level) as z:
        for name in sorted(files):
            z.writestr(name, files[name])
    os.replace(tmp, path)


def pack_mcmeta(pack_format: int, description: str) -> bytes:
    return json.dumps({"pack": {"pack_format": pack_format, "description": description}}, indent=2).encode()


def is_texture(name: str) -> bool:
    """A texture or its .mcmeta in the modern layout (``assets/<namespace>/textures/...``)."""
    return name.startswith("assets/") and "/textures/" in name and name.endswith((".png", ".png.mcmeta"))


def is_legacy_texture(name: str) -> bool:
    """Minecraft 1.4/1.5 mods put textures anywhere in the jar; 1.4 animations are described by a ``.txt`` beside them."""
    return not name.startswith("META-INF/") and name.endswith((".png", ".txt"))


class SourceIndex:
    """Which archive holds the original of each texture.

    Archives are added in priority order and the first one to hold a path wins, so add the game jar first and the
    mod jars after it. Archives stay open for the life of the index.
    """

    def __init__(self) -> None:
        self._where: dict[str, zipfile.ZipFile] = {}
        self.archives: list[str] = []

    @classmethod
    def build(
        cls,
        vanilla_jar: str | os.PathLike | None = None,
        mods_dir: str | os.PathLike | None = None,
        accept: Callable[[str], bool] = is_texture,
    ) -> "SourceIndex":
        index = cls()
        if vanilla_jar:
            index.add(vanilla_jar, accept)
        if mods_dir:
            for jar in sorted(Path(mods_dir).iterdir()):
                if jar.suffix.lower() in (".jar", ".zip") and jar.is_file():
                    index.add(jar, accept)
        return index

    def add(self, archive: str | os.PathLike, accept: Callable[[str], bool] = is_texture) -> int:
        """Index one archive; returns how many new paths it contributed (0 for an unreadable archive)."""
        try:
            z = zipfile.ZipFile(archive)
        except (OSError, zipfile.BadZipFile):
            return 0
        added = 0
        for name in z.namelist():
            if accept(name) and name not in self._where:
                self._where[name] = z
                added += 1
        self.archives.append(str(archive))
        return added

    def __contains__(self, name: str) -> bool:
        return name in self._where

    def names(self) -> Iterable[str]:
        return self._where.keys()

    def read(self, name: str) -> bytes | None:
        z = self._where.get(name)
        return z.read(name) if z else None

    def origin(self, name: str) -> str | None:
        z = self._where.get(name)
        return z.filename if z else None
