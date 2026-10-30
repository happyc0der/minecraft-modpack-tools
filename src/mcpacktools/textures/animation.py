"""Animation metadata: reading ``.mcmeta`` animation blocks and the frame geometry of a texture strip."""

from __future__ import annotations

import json

from mcpacktools.packfiles import png_size


def animation(mcmeta: bytes | None) -> dict | None:
    """The ``animation`` object of an .mcmeta, or None. Note ``"animation": {}`` is a valid (default) animation."""
    if not mcmeta:
        return None
    try:
        meta = json.loads(mcmeta.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        return None
    anim = meta.get("animation") if isinstance(meta, dict) else None
    return anim if isinstance(anim, dict) else None


def geometry(png: bytes, mcmeta: bytes | None) -> tuple[int, int, int] | None:
    """(width, frame_height, frame_count) of a vertical animation strip, or None if the layout isn't a plain strip.

    Frames are square unless the .mcmeta says otherwise; strips whose frames are narrower than the image are not
    handled (nothing in practice uses them).
    """
    size = png_size(png)
    if not size:
        return None
    width, height = size
    anim = animation(mcmeta) or {}
    frame_w, frame_h = anim.get("width", width), anim.get("height", width)
    if frame_w != width or frame_h <= 0 or height % frame_h:
        return None
    return width, frame_h, height // frame_h


def legacy_strip(png: bytes) -> tuple[int, int, int] | None:
    """Minecraft 1.4/1.5 animations: square frames stacked vertically, described by a .txt next to the PNG."""
    size = png_size(png)
    if not size or size[1] <= size[0] or size[1] % size[0]:
        return None
    return size[0], size[0], size[1] // size[0]
