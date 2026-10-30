# minecraft-modpack-tools

Command-line tools for the jobs that big Minecraft modpacks leave you to do by hand: making an HD texture pack
actually cover a modpack, fixing the texture bugs that HD packs cause, repairing resource packs that render
purple/black on Minecraft 26.x, moving a player's progress to another account, and finding which copy of a world
is the newest.

Everything is one command, `mcpack`, with a subcommand per job. Each one has been run on real packs and worlds
(Minecraft 1.4.7 to 26.2) before being cleaned up for release, and none of them needs the game to be running.

| Command | What it does | Versions |
|---|---|---|
| [`mcpack upscale`](#build-an-hd-fill-pack-for-a-modpack) | Upscales every mod texture your HD pack (Faithful etc.) lacks into a "fill" pack, with the rules that keep upscaled textures looking right | any (1.4/1.5 with `--legacy`) |
| [`mcpack animated`](#fix-animated-textures-in-an-hd-pack) | Rebuilds every animated texture (fire, lava, water, portals, machines) frame by frame | 1.6+ |
| [`mcpack atlas-fit`](#fix-stitcherexception-unable-to-fit) | Fixes the `StitcherException: Unable to fit` start-up crash by shrinking oversized textures | 1.6–1.12 mostly |
| [`mcpack item-atlas`](#fix-purpleblack-items-on-26x) | Finds and fixes purple/black items ("Multiple atlases used in model") in resource packs | 26.x |
| [`mcpack player-transfer`](#move-a-players-data-to-another-uuid) | Moves a player's inventory, stats, advancements and every mod record that names them onto another UUID | any |
| [`mcpack worlds`](#find-every-copy-of-every-world) | Finds every copy of every world in folders and backup zips, and compares two locations | any |
| [`mcpack xbrz`](#optional-xbrz-for-the-best-upscaling) | Builds the optional xBRZ library for the best upscaling quality | – |

**AI agents:** read [AGENTS.md](AGENTS.md). It tells you which command fits which request, the safe order of
steps, and how to find the files a command needs.

## Install

Python 3.10 or newer. With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/happyc0der/minecraft-modpack-tools
```

or with pip: `pip install git+https://github.com/happyc0der/minecraft-modpack-tools`. Then run `mcpack --help`,
and `mcpack <command> --help` for every option of a command.

### Optional: xBRZ for the best upscaling

The texture commands upscale with **xBRZ** when its library is available, and with **Scale2x** (built in, no setup)
otherwise. xBRZ gives clearly better curves and diagonals. It is GPL-3.0, so it isn't bundled; build it once:

```bash
mcpack xbrz build
mcpack xbrz status
```

That downloads xBRZ's source at a pinned, hash-checked version and compiles it with `g++`, `clang++` or MSVC's `cl`.
On Windows the easiest compiler is MSYS2's UCRT64 g++; run the build from an MSYS2 or Git Bash shell that has it on
`PATH`. The library goes to `%LOCALAPPDATA%\mcpacktools\xbrz.dll` (Windows) or `~/.local/share/mcpacktools/`
(Linux/macOS); set `MCPACK_XBRZ` to use one somewhere else.

## Recipes

Texture commands that change a pack rewrite the zip **in place** (atomically: they write `<pack>.tmp`, then swap it
in). Keep a copy of the original until you've checked the result in game. Close the game before editing a world.

### Build an HD fill pack for a modpack

Your HD pack covers vanilla and some mods; this upscales the rest, so nothing in the modpack stays 16x.

```bash
mcpack upscale --mods path/to/instance/mods --skip "path/to/Faithful 32x.zip" \
               --factor 2 --pack-format 3 --out "Upscaled mod textures.zip"
```

Put the fill pack **below** the HD pack in the resource-pack list, so real HD art wins wherever both have a texture.
`--factor` 2 matches a 32x pack, 4 matches a 64x pack. `--pack-format` is the game's resource-pack format, for
example 1 for 1.6–1.8, 2 for 1.9–1.10, 3 for 1.11–1.12, 6 for 1.16.2–1.16.5, 15 for 1.20–1.20.1.

What "the rules" means, and why each one exists, is written down in
[docs/texture-rules.md](docs/texture-rules.md): animation strips are scaled frame by frame, tiling textures stay
seamless, cut-out textures stay cut-out, leaves keep their noise, and transparent pixels get bled colour so mipmaps
don't draw halos.

**1.4.7 / 1.5** load only one texture pack, and their mods keep textures anywhere in the jar:

```bash
mcpack upscale --legacy --mods path/to/mods --merge-into "My HD pack.zip" --out "My HD pack + mods.zip" --factor 4
```

### Fix animated textures in an HD pack

Symptoms: white or flat blobs in fire, specks at the top of lava or water, seams in portals, blocky edges on
animated cut-outs. They come from animation strips that were upscaled as one image.

```bash
mcpack animated "My 64x pack.zip" --vanilla path/to/client.jar --mods path/to/mods \
                --base "Faithful 32x.zip" --dry-run
mcpack animated "My 64x pack.zip" --vanilla path/to/client.jar --mods path/to/mods \
                --base "Faithful 32x.zip" --preview before-after.png
```

`--base` packs (lower-res, hand-drawn) are preferred over the 16x originals when they hold the same animation. Art
the pack got from somewhere else is detected and left alone. Fire's white core is tinted toward the flame's colour;
`--no-warm` keeps it white.

### Fix "StitcherException: Unable to fit"

The game crashes at start-up because the pack's block and item textures don't fit the texture atlas (16384 x 16384
on most GPUs, before 1.13 shared by every mod).

```bash
mcpack atlas-fit "My 64x pack.zip"            # how full the atlas would be
mcpack atlas-fit "My 64x pack.zip" --cap 128  # shrink every block/item texture wider than 128 px
```

If the crash names a texture of a mod that stitches other images into the atlas (Betweenlands does), add its folder:
`--extra assets/thebetweenlands/textures/tiles/ assets/thebetweenlands/textures/entity/`.

### Fix purple/black items on 26.x

```bash
mcpack item-atlas plugin-pack.zip --client path/to/client-26.2.jar --check --list
mcpack item-atlas plugin-pack.zip fixed-pack.zip --client path/to/client-26.2.jar
```

The check simulates how the game bakes every item and block model and reports the same failures the game would log.
The fix registers textures in the right atlas, repairs broken model references, and gives items an items-atlas copy
of textures that must stay in the blocks atlas. Details: [docs/item-atlas.md](docs/item-atlas.md).

### Move a player's data to another UUID

For example, an offline-mode name moving to a real account on a server, or an old singleplayer world you want to
play as yourself:

```bash
mcpack player-transfer path/to/world --from offline:OldName --to 123e4567-e89b-12d3-a456-426614174000 \
                       --from-name OldName --to-name NewName --dry-run
```

Drop `--dry-run` once the list of changes looks right. Inventory, ender chest, XP, position, stats, advancements,
mod player files, and every UUID reference in the world's mod data and chunks (owners of machines, pets, teams,
quest progress) move to the target. The target's own items are kept in free slots. Every changed file is backed up
first; the source's files are left in place. Details and safety notes: [docs/player-transfer.md](docs/player-transfer.md).

### Find every copy of every world

```bash
mcpack worlds "D:/old drive"                         # every world, its newest copy, how many copies
mcpack worlds "D:/old drive" --against ~/.minecraft  # which exist only on the old drive, or are newer there
```

Worlds are matched by seed and name from level.dat, inside folders and inside backup zips.

## Where things are

* **Client jar** (`--vanilla`, `--client`): `.minecraft/versions/<version>/<version>.jar` for the official launcher;
  `ATLauncher/libraries/net/minecraft/client/<version>/client-<version>.jar`; Prism/MultiMC:
  `libraries/com/mojang/minecraft/<version>/minecraft-<version>-client.jar`.
* **Mods folder** (`--mods`): the instance's `mods/` (in the instance folder, or `.minecraft/mods`).
* **Worlds**: the instance's `saves/<world>` for singleplayer; the server's world folder (`level-name` in
  `server.properties`).

## Licences of what you build

These tools don't include any game assets or pack art. Packs you build from other people's art (Faithful, mods'
textures) are covered by those works' licences: fine for your own use, check before sharing them.

## Development

```bash
git clone https://github.com/happyc0der/minecraft-modpack-tools
cd minecraft-modpack-tools
uv sync
uv run pytest
```

The tests build small synthetic packs, jars and worlds, so they need no game files. Layout:

```
src/mcpacktools/
  cli.py              the mcpack command (one module per subcommand registers itself)
  packfiles.py        pack zips: read, write atomically, index original textures in jars
  scaling.py          xBRZ / Scale2x scalers and the texture rules
  nbt.py              byte-exact NBT and Anvil region files
  itematlas.py        mcpack item-atlas
  xbrz_build.py       mcpack xbrz
  textures/           mcpack upscale, animated, atlas-fit
  world/              mcpack player-transfer (players.py, uuids.py), worlds (finder.py)
tests/                pytest suite with synthetic fixtures
docs/                 background for the texture rules, item atlas and player transfer
```

## Credits

xBRZ by Zenju, with the C export by io mintz ([iomintz/xbrz.py](https://github.com/iomintz/xbrz.py)), downloaded
and built on your machine by `mcpack xbrz build`. Scale2x/Scale3x after Andrea Mazzoleni's AdvanceMAME algorithm.

Not affiliated with Mojang or Microsoft.

## License

Licensed under the [Apache License 2.0](LICENSE).
