"""Pixel-art upscaling for Minecraft textures.

Two scalers are available:

* **xBRZ** (best quality), through a shared library you build once with ``mcpack xbrz build``. xBRZ is GPL-3.0, so
  it is never bundled; it is loaded at run time from ``$MCPACK_XBRZ`` or the default location :func:`default_xbrz_path`.
* **Scale2x / Scale3x** (EPX family), pure NumPy, always available. Factors 2, 3, 4, 6, 8 and 9 are composed from
  them; any other factor falls back to nearest neighbour.

On top of the raw scaler, :func:`upscale_frame` applies the rules that keep upscaled textures looking right in game:

1. Opaque textures (stone, lava, water, fluids) are scaled as a 3x3 tiled copy and the centre is cut out, so they still
   tile seamlessly. Scaling a lone tile smooths its edges against nothing and leaves visible seams.
2. Cut-out textures (alpha only 0 or 255: leaves, plants, items) stay cut-out. The smooth alpha the scaler produces is
   thresholded at 128; semi-transparent edges render wrong on cut-out render layers.
3. Leaves keep their noise (nearest neighbour), and the colour under their holes stays black, because on Fast graphics
   the game draws leaves opaque and shows those pixels.
4. The colour under every fully transparent pixel is copied from the nearest visible pixel ("alpha bleed"), so mipmaps
   and filtering don't pull dark or white halos into the edges.

Animation strips must be scaled one frame at a time (:func:`upscale_strip`); scaled as one image, neighbouring frames
bleed into each other at every frame border.
"""

from __future__ import annotations

import ctypes
import io
import os
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

LEAVES = re.compile(r"leaves|/leaf", re.IGNORECASE)


# --------------------------------------------------------------------------------------------- scalers


def default_xbrz_path() -> Path:
    """Where ``mcpack xbrz build`` puts the library and where it is looked for by default."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        name = "xbrz.dll"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        name = "libxbrz.dylib" if sys.platform == "darwin" else "libxbrz.so"
    return base / "mcpacktools" / name


class XbrzScaler:
    """xBRZ through the C function ``xbrz_scale_defaults`` exported by io mintz's build of Zenju's xBRZ."""

    name = "xbrz"
    max_factor = 6
    _RGBA = 2  # xbrz::ColorFormat::RGBA (straight alpha, bytes in R, G, B, A order)

    def __init__(self, library: str | os.PathLike) -> None:
        self.library = str(library)
        self._lib = None

    def _load(self):
        # Loaded lazily so the scaler can be sent to worker processes before the library is opened.
        if self._lib is None:
            lib = ctypes.CDLL(self.library)
            fn = lib.xbrz_scale_defaults
            fn.argtypes = [ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
                           ctypes.c_int, ctypes.c_int, ctypes.c_int]
            fn.restype = None
            self._lib = lib
        return self._lib

    def __getstate__(self):
        return {"library": self.library, "_lib": None}

    def scale(self, rgba: np.ndarray, factor: int) -> np.ndarray:
        if not 2 <= factor <= self.max_factor:
            return nearest(rgba, factor)
        h, w = rgba.shape[:2]
        src = (ctypes.c_uint32 * (w * h)).from_buffer_copy(np.ascontiguousarray(rgba, np.uint8).tobytes())
        dst = (ctypes.c_uint32 * (w * h * factor * factor))()
        self._load().xbrz_scale_defaults(factor, src, dst, w, h, self._RGBA)
        return np.frombuffer(bytes(dst), np.uint8).reshape(h * factor, w * factor, 4).copy()


class EpxScaler:
    """Scale2x / Scale3x (AdvMAME), composed for 4x, 6x, 8x and 9x. Pure NumPy; edges repeat the border pixel."""

    name = "scale2x"
    _PLANS = {2: (2,), 3: (3,), 4: (2, 2), 6: (2, 3), 8: (2, 2, 2), 9: (3, 3)}

    def scale(self, rgba: np.ndarray, factor: int) -> np.ndarray:
        plan = self._PLANS.get(factor)
        if plan is None:
            return nearest(rgba, factor)
        out = np.ascontiguousarray(rgba, np.uint8)
        for step in plan:
            out = _scale2x(out) if step == 2 else _scale3x(out)
        return out


def _neighbours(rgba: np.ndarray):
    """The 3x3 neighbourhood of every pixel as uint32 colour keys, edges repeated: A B C / D E F / G H I."""
    key = np.ascontiguousarray(rgba).view(np.uint32)[:, :, 0]
    p = np.pad(key, 1, mode="edge")
    h, w = key.shape
    s = lambda dy, dx: p[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]
    return (s(-1, -1), s(-1, 0), s(-1, 1), s(0, -1), key, s(0, 1), s(1, -1), s(1, 0), s(1, 1))


def _assemble(rgba: np.ndarray, picks: list[list[np.ndarray]], n: int) -> np.ndarray:
    """picks[r][c] = HxW uint32 keys for output sub-pixel (r, c); returns the (H*n)x(W*n) RGBA image."""
    h, w = rgba.shape[:2]
    out = np.empty((h * n, w * n), np.uint32)
    for r in range(n):
        for c in range(n):
            out[r::n, c::n] = picks[r][c]
    return out.view(np.uint8).reshape(h * n, w * n, 4)


def _scale2x(rgba: np.ndarray) -> np.ndarray:
    a, b, c, d, e, f, g, h, i = _neighbours(rgba)
    cond = (b != h) & (d != f)
    e0 = np.where(cond & (d == b), d, e)
    e1 = np.where(cond & (b == f), f, e)
    e2 = np.where(cond & (d == h), d, e)
    e3 = np.where(cond & (h == f), f, e)
    return _assemble(rgba, [[e0, e1], [e2, e3]], 2)


def _scale3x(rgba: np.ndarray) -> np.ndarray:
    a, b, c, d, e, f, g, h, i = _neighbours(rgba)
    cond = (b != h) & (d != f)
    db, bf, dh, hf = cond & (d == b), cond & (b == f), cond & (d == h), cond & (h == f)
    e0 = np.where(db, d, e)
    e1 = np.where((db & (e != c)) | (bf & (e != a)), b, e)
    e2 = np.where(bf, f, e)
    e3 = np.where((db & (e != g)) | (dh & (e != a)), d, e)
    e5 = np.where((bf & (e != i)) | (hf & (e != c)), f, e)
    e6 = np.where(dh, d, e)
    e7 = np.where((dh & (e != i)) | (hf & (e != g)), h, e)
    e8 = np.where(hf, f, e)
    return _assemble(rgba, [[e0, e1, e2], [e3, e, e5], [e6, e7, e8]], 3)


def nearest(rgba: np.ndarray, factor: int) -> np.ndarray:
    return np.kron(rgba, np.ones((factor, factor, 1), np.uint8)).astype(np.uint8)


def get_scaler(choice: str = "auto"):
    """``auto`` = xBRZ if its library is present, else Scale2x. ``xbrz`` fails loudly when the library is missing."""
    path = Path(os.environ.get("MCPACK_XBRZ") or default_xbrz_path())
    if choice in ("auto", "xbrz") and path.is_file():
        return XbrzScaler(path)
    if choice == "xbrz":
        raise SystemExit(f"xBRZ library not found at {path}. Build it with 'mcpack xbrz build' or set MCPACK_XBRZ.")
    if choice == "nearest":
        return NearestScaler()
    return EpxScaler()


class NearestScaler:
    name = "nearest"

    def scale(self, rgba: np.ndarray, factor: int) -> np.ndarray:
        return nearest(rgba, factor)


# --------------------------------------------------------------------------------------------- texture rules


def is_binary_alpha(alpha: np.ndarray) -> bool:
    return bool(np.isin(alpha, (0, 255)).all())


def bleed(rgba: np.ndarray) -> np.ndarray:
    """Fill the colour of fully transparent pixels from the nearest visible pixel (in place; also returned)."""
    clear = rgba[:, :, 3] == 0
    if clear.any() and not clear.all():
        idx = ndimage.distance_transform_edt(clear, return_distances=False, return_indices=True)
        rgba[:, :, :3][clear] = rgba[:, :, :3][idx[0], idx[1]][clear]
    return rgba


def tiled(rgba: np.ndarray, factor: int, scaler) -> np.ndarray:
    """Scale a 3x3 tiling of the texture and keep the centre, so the result still tiles seamlessly."""
    h, w = rgba.shape[:2]
    big = scaler.scale(np.tile(rgba, (3, 3, 1)), factor)
    return np.ascontiguousarray(big[h * factor:2 * h * factor, w * factor:2 * w * factor])


def upscale_frame(rgba: np.ndarray, factor: int, scaler, *, leaves: bool = False) -> np.ndarray:
    """One texture (or one animation frame), with the rules from the module docstring."""
    alpha = rgba[:, :, 3]
    if leaves:
        out = nearest(rgba, factor)
        out[:, :, :3][out[:, :, 3] == 0] = 0
        return out
    if alpha.min() == 255:
        out = tiled(rgba, factor, scaler)
        out[:, :, 3] = 255
        return out
    out = scaler.scale(rgba, factor)
    if is_binary_alpha(alpha):
        out[:, :, 3] = np.where(out[:, :, 3] >= 128, 255, 0).astype(np.uint8)
    return bleed(out)


def upscale_strip(rgba: np.ndarray, frame_height: int, factor: int, scaler, *, leaves: bool = False) -> np.ndarray:
    """A vertical animation strip, frame by frame."""
    frames = [upscale_frame(rgba[y:y + frame_height], factor, scaler, leaves=leaves)
              for y in range(0, rgba.shape[0], frame_height)]
    return np.concatenate(frames, axis=0)


# --------------------------------------------------------------------------------------------- PNG helpers


def decode(png: bytes) -> np.ndarray:
    return np.array(Image.open(io.BytesIO(png)).convert("RGBA"))


def encode(rgba: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgba, np.uint8), "RGBA").save(buf, "PNG", compress_level=9, optimize=True)
    return buf.getvalue()
