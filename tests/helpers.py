"""Builders for synthetic packs, jars and worlds used by the tests."""

from __future__ import annotations

import gzip
import io
import json
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from mcpacktools import nbt
from mcpacktools.nbt import Compound, ListTag, Root


def png(rgba: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgba, np.uint8), "RGBA").save(buf, "PNG")
    return buf.getvalue()


def solid(h: int, w: int, color=(200, 50, 50, 255)) -> np.ndarray:
    a = np.zeros((h, w, 4), np.uint8)
    a[:, :] = color
    return a


def make_zip(path: Path, files: dict[str, bytes | str | dict]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            if isinstance(data, dict):
                data = json.dumps(data)
            z.writestr(name, data)
    return path


def compound(**fields) -> Compound:
    """compound(Name=(nbt.STRING, b"x"), ...)"""
    return Compound([[k.encode(), t, v] for k, (t, v) in fields.items()])


def write_nbt(path: Path, value: Compound, gz: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = nbt.dumps(Root(b"", nbt.COMPOUND, value))
    path.write_bytes(gzip.compress(data) if gz else data)


def read_nbt(path: Path) -> Compound:
    return nbt.read_file(path)[0].value


def item(slot: int, ident: str, count: int = 1) -> Compound:
    return compound(Slot=(nbt.BYTE, slot), id=(nbt.STRING, ident.encode()), Count=(nbt.BYTE, count))


def inventory(*items: Compound) -> ListTag:
    return ListTag(nbt.COMPOUND, list(items))
