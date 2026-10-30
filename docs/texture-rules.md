# Upscaling Minecraft textures: the rules and why

Upscaling pixel art for Minecraft is not just "run xBRZ on every PNG". Each rule below was added after it went
wrong in a real pack. They live in `src/mcpacktools/scaling.py` and are used by `upscale` and `animated`.

## 1. Animation strips are scaled one frame at a time

An animated texture is a vertical strip of frames plus an `.mcmeta` (1.4/1.5: a `.txt`). Scaled as one image, the
scaler blends the bottom row of each frame into the top row of the next. In game that shows as specks at the top of
fire, flickering lines in lava and water, and seams in portal swirls. `upscale_strip` cuts the strip into frames,
scales each, and stacks them again. The `.mcmeta` is copied unchanged: frame size follows the image width, so the
same timing data works at any resolution.

## 2. Opaque textures stay tileable

Blocks repeat across the world, so a texture's left edge must continue its right edge. A scaler that sees one tile
smooths its borders against nothing, and the result shows a grid. `tiled` scales a 3x3 copy of the texture and keeps
the centre, so every edge is smoothed against its real neighbour.

## 3. Cut-outs stay cut-out

Leaves, plants, glass panes, items and fire use alpha that is only fully clear or fully solid. Scalers produce
smooth, semi-transparent edges, which the game's cut-out render layers draw as dark or white fringes (or not at all).
When the source alpha is binary, the scaled alpha is thresholded at 128.

## 4. Leaves keep their noise

Leaves are deliberately noisy; any smoothing scaler turns them into blobs. Paths matching `leaves` or `/leaf` are
scaled with nearest neighbour. On Fast graphics the game draws leaves opaque, so the colour under their holes is
visible: it is kept black, as vanilla does.

## 5. Transparent pixels get bled colour

Pixels with alpha 0 still have a colour, and mipmaps and texture filtering average it into the visible edge. Left as
black (or as the scaler's leftovers), it draws a dark or bright halo around cut-outs at a distance. `bleed` copies
the colour of the nearest visible pixel into every clear pixel.

## 6. Hand-drawn art wins

When a lower-resolution hand-drawn texture exists (a 32x base pack for a 64x pack), it is scaled instead of the 16x
original: an artist's 32x frames upscaled 2x look far better than 16x art upscaled 4x. `animated` checks that the
base pack's texture really is the same art before using it: the first frame, shrunk to the original's size, must
cover the same shape (overlap of at least 75%) with similar colours, since mods often redraw textures between
versions. Base packs redraw fire freely, so for fire only the average colour has to match.

It also never replaces art it can't account for: a strip is only rebuilt from the 16x original if the pack's current
strip is itself just an upscale of that original. Anything else came from an artist and is kept.

## 7. Fire's white core is tinted

A flame has a few near-white pixels at its hottest point. Upscaled, they become large flat white shapes that read as
holes or blobs. `animated` tints pixels whose every channel is above 225 towards the flame's own brightest colour
(cream for vanilla fire, pale blue for soul fire), by up to 75%: still the brightest part of the flame, but read as
flame. `--no-warm` turns this off.

## 8. Mind the atlas

Before 1.13, all block and item textures of every mod are stitched into one atlas, at most 16384 x 16384 on most
GPUs, with each sprite rounded up to a multiple of 2^mipmap-levels. A 4x upscale of a big modpack, plus a few
512-2048 px textures, overflows it, and the game crashes with `StitcherException: Unable to fit`. `atlas-fit`
estimates the usage and caps oversized sprites (128 px keeps 64x textures intact).

## Scalers

xBRZ (factors 2–6) gives the best results on pixel art. Scale2x/Scale3x (EPX) is built in: it keeps edges hard and
never invents colours, but rounds diagonals less. Factors 4, 6, 8 and 9 are compositions of 2 and 3; others fall back
to nearest neighbour.
