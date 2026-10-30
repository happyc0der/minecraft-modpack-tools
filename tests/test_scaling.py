import numpy as np
import pytest

from mcpacktools import scaling
from mcpacktools.scaling import EpxScaler, bleed, upscale_frame, upscale_strip
from helpers import solid


@pytest.mark.parametrize("factor", [2, 3, 4, 6])
def test_epx_keeps_flat_colour_and_size(factor):
    img = solid(8, 8, (10, 20, 30, 255))
    out = EpxScaler().scale(img, factor)
    assert out.shape == (8 * factor, 8 * factor, 4)
    assert (out == img[0, 0]).all()


def test_scale2x_rounds_a_diagonal():
    a, b = (255, 0, 0, 255), (0, 0, 255, 255)
    img = np.array([[a, b], [b, b]], np.uint8)
    out = EpxScaler().scale(img, 2)
    # the bottom-right sub-pixel of the red pixel takes the blue neighbours' colour (EPX rule 3)
    assert tuple(out[1, 1]) == b and tuple(out[0, 0]) == a


def test_cutout_alpha_stays_binary():
    img = solid(16, 16)
    img[4:12, 4:12, 3] = 0
    out = upscale_frame(img, 4, EpxScaler())
    assert set(np.unique(out[:, :, 3])) <= {0, 255}


def test_opaque_texture_still_tiles():
    img = solid(8, 8, (0, 0, 0, 255))
    img[:, :4] = (255, 255, 255, 255)  # half black, half white: tiles as stripes
    out = upscale_frame(img, 2, EpxScaler())
    # straight stripes scale to straight stripes, with no smoothing at the tile's own borders
    np.testing.assert_array_equal(out, scaling.nearest(img, 2))


def test_strip_frames_do_not_bleed_into_each_other():
    strip = np.concatenate([solid(8, 8, (255, 0, 0, 255)), solid(8, 8, (0, 255, 0, 255))])
    out = upscale_strip(strip, 8, 4, EpxScaler())
    assert out.shape == (64, 32, 4)
    assert (out[:32] == (255, 0, 0, 255)).all() and (out[32:] == (0, 255, 0, 255)).all()


def test_bleed_fills_hidden_colour():
    img = solid(4, 4, (9, 9, 9, 255))
    img[:, 2:] = (0, 0, 0, 0)
    out = bleed(img.copy())
    assert (out[:, 2:, :3] == 9).all() and (out[:, 2:, 3] == 0).all()


def test_leaves_keep_their_noise_and_dark_holes():
    img = solid(4, 4, (40, 160, 40, 255))
    img[0, 0] = (255, 255, 255, 0)
    out = upscale_frame(img, 2, EpxScaler(), leaves=True)
    assert (out[:2, :2] == 0).all()


@pytest.mark.skipif(not scaling.default_xbrz_path().is_file(), reason="xBRZ library not built")
def test_xbrz_scaler_shape():
    out = scaling.XbrzScaler(scaling.default_xbrz_path()).scale(solid(8, 8), 4)
    assert out.shape == (32, 32, 4)
