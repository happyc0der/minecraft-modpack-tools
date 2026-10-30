# Using minecraft-modpack-tools as an agent

This file is for AI coding agents helping someone with a Minecraft modpack, resource pack or world. It covers which
command fits which request, the order to run things in, and the checks that keep a player's files safe.

## Setup

```bash
uv tool install git+https://github.com/happyc0der/minecraft-modpack-tools   # or: pip install git+...
mcpack --help
mcpack xbrz status          # which upscaler is active
mcpack xbrz build           # optional, best quality; needs g++/clang++/cl (on Windows: run it from MSYS2 or Git Bash)
```

Working on this repo instead: `uv sync`, then `uv run mcpack ...` and `uv run pytest`.

## Pick the command

| The user says... | Command |
|---|---|
| "some mod textures are still low-res with my HD pack", "make my 32x/64x pack cover this modpack" | `mcpack upscale` |
| "fire has white blobs", "lava/water has specks or seams", "animated blocks look wrong in my HD pack" | `mcpack animated` |
| crash log has `StitcherException: Unable to fit` | `mcpack atlas-fit` |
| 26.x: items are purple/black; log has `Multiple atlases used in model` or `Missing textures in model` | `mcpack item-atlas` |
| "move my old character / offline name / friend's progress to my account", "import player X's data onto me" | `mcpack player-transfer` |
| "which of these old worlds don't I have elsewhere?", recovered drive, cleaning up backups | `mcpack worlds` |

Every command has `--help` with the full background (the module docstring); read it before an unusual run.

## Ground rules

1. **Close the game and any server using the files.** `player-transfer` refuses a world whose `session.lock` is
   held (Windows); on Linux/macOS you must check yourself.
2. **Dry run first, show the user, then run.** `upscale`, `animated`, `atlas-fit` and `player-transfer` have
   `--dry-run`; `item-atlas` has `--check`. Summarise what will change before writing.
3. **Packs are rewritten in place** (`animated`, `atlas-fit`). Copy the zip first unless the user already has a
   backup. `upscale` and `item-atlas` write a new file.
4. **World edits back up every file they change** to `--backup-dir` (default `<world>.backup-<time>` beside the
   world). Tell the user where it is. Source player files are never deleted.
5. **Exit codes:** 0 = done; `item-atlas --check` returns 1 when something fails to bake (that's a finding, not an
   error). `SystemExit` messages explain what to fix (missing library, nil UUID without permission, world open...).
6. Don't share packs built from other people's art (Faithful, mod textures) on the user's behalf; they are fine
   for the user's own use.

## Finding the inputs

* **Client jar** (`--vanilla`, `--client`), by launcher:
  official `.minecraft/versions/<v>/<v>.jar`; ATLauncher `ATLauncher/libraries/net/minecraft/client/<v>/client-<v>.jar`;
  Prism/MultiMC `libraries/com/mojang/minecraft/<v>/minecraft-<v>-client.jar`. Search for `client-<v>.jar` or
  `<v>.jar` if unsure. The version is in the instance's config (`instance.json`, `mmc-pack.json`) or the crash log.
* **Mods folder**: `<instance>/mods` (ATLauncher `instances/<name>/mods`, Prism `instances/<name>/.minecraft/mods`).
* **Resource packs**: `<instance>/resourcepacks`; 1.4/1.5: `texturepacks`. Which ones are active, top first: the
  `resourcePacks:` line of `options.txt` (1.4/1.5: `skin:`).
* **pack_format** for `upscale --pack-format`: 1 (1.6–1.8), 2 (1.9–1.10), 3 (1.11–1.12), 4 (1.13–1.14),
  5 (1.15–1.16.1), 6 (1.16.2–1.16.5), 15 (1.20–1.20.1). For others, copy it from a pack that already works on that
  version (its `pack.mcmeta`).
* **Player UUIDs**: online accounts from `usercache.json` (servers) or the player's profile; offline-mode players:
  `offline:<name>` is accepted directly. Singleplayer host data lives in `level.dat` (`Data.Player`), the
  others in `playerdata/<uuid>.dat`.

## Command notes

### upscale
- `--skip` every HD pack the user loads above the fill pack; anything they cover is left out.
- `--factor` = HD resolution / 16 (2 for 32x, 4 for 64x). `--max-size 256` skips art that's already high-res.
- 1.4/1.5: `--legacy --merge-into <their single pack> --out <new zip>`, then select the new zip in game.
- After building: tell the user to put the fill pack below their HD pack. If the game then crashes with a
  StitcherException, run `atlas-fit` on the biggest pack.

### animated
- Needs `--vanilla`; pass `--mods` and every lower-res hand-drawn `--base` pack you have (e.g. Faithful 32x for a
  64x pack). Base packs made for another game version: add `--base-vanilla-only`; for 1.13+ games with 1.12 base
  packs add `--modern-names`.
- Output counters: "base pack" / "original" = rebuilt from that source; "pack art kept" = the pack's own art,
  untouched; "already wide enough" = nothing to do. `--preview sheet.png` shows before/after.

### atlas-fit
- Without `--cap` it only reports how full the atlas would be (an upper bound). Above ~95%, cap at 128 (keeps 64x
  textures sharp; only oversized art shrinks). Crash names a mod texture packed by the mod itself (Betweenlands
  `packed_model_texture`)? Add that mod's folders with `--extra`.

### item-atlas
- Always `--check` first and quote the counts. Fix into a NEW file, never over the original.
- "textures referenced but in neither pack nor game" can't be fixed here: those files are missing from the pack.
- `--copy-namespace` sets where items-atlas copies go (default `atlasfix`).

### player-transfer
- `--from` is the old player, `--to` the one who should own everything. Pass `--from-name/--to-name` too when you
  know them: names next to the UUID (team titles, profiles) and files named after the player get renamed.
- The all-zero UUID (`00000000-...`) is what some offline setups give a player, but many mods also use it for
  "nobody". It needs `--allow-nil`, and region changes need `--only-ids <regex of block entity/entity ids>`; read
  the dry-run list with the user and pick ids that really were that player's (machines, pets they placed).
- Where both players have a record in a per-player list or map, the source's wins (`--keep-target-records` flips it).
- The target's items go into free slots; if they don't fit it stops and says so (`--replace-target-items` drops them).
- On servers, also check `ops.json`, `whitelist.json`, `usercache.json` and permission plugins; those are outside
  the world folder and not touched.

### worlds
- `mcpack worlds <roots>` lists; `--against <other roots>` sorts into ONLY HERE / NEWER HERE / ALSO THERE.
  Matching is by seed + name, so a world renamed in game counts as different.

## Contributing

- One module per subcommand, each with `add_parser(sub)` and `run(args)`; register it in `cli.py`.
- The module docstring is the command's `--help` description: keep it the full story of the problem and the fix.
- Tests use synthetic fixtures (`tests/helpers.py`); never commit game files or pack art. `uv run pytest` must pass.
- NBT edits must round-trip byte for byte (`nbt.read_file` refuses files that don't), and every write goes through a
  temp file and `os.replace`.
