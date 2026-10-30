# Moving a player's data to another UUID

Minecraft identifies players by UUID, and so does almost every mod. Renaming a file in `playerdata/` moves the
inventory but leaves behind everything the world remembers about the player: team membership, quest progress,
ownership of machines, tamed animals, power networks, claimed rewards. `mcpack player-transfer` moves all of it.

## What it touches

| Scope | Files | What happens |
|---|---|---|
| `player` | `playerdata/<from>.dat` | copied to `<to>.dat` with the UUID inside rewritten; the target's own items are added into free slots |
| | `level.dat` `Data.Player` | singleplayer host record (it wins over playerdata): replaced when it belongs to either player, or with `--host` |
| | `stats/`, `advancements/` | merged: stats summed, advancements combined |
| | other files named `<from>` | copied to the `<to>` name (mod player files: Baubles, Cyclic, ...) |
| `data` | `data/**/*.dat`, `data/**/*.json` (all dimensions) | every UUID reference rewritten; files named after the source player are written under the target's name |
| `regions` | `region/*.mca`, `entities/*.mca` (all dimensions) | every UUID reference in chunk NBT rewritten |

Not touched: files outside the world folder (`ops.json`, `whitelist.json`, `usercache.json`, plugin databases).

## How UUIDs are found

| Encoding | Used by |
|---|---|
| `<prefix>Most` / `<prefix>Least` LONG pair | 1.15 and older, most mods (`UUIDMost`, `ownerMost`, `OwnerUUIDMost`...) |
| `<prefix>U` / `<prefix>L` LONG pair | Extra Utilities 2 |
| INT_ARRAY of 4 | 1.16+ (`UUID: [I; ...]`) |
| string, dashed or not, alone or inside a string | many mods (`OwnerUUID`, `"player:<uuid>"` icons, JSON) |
| compound key or JSON key equal to the UUID | per-player maps (quest rewards, profiles) |

Where a per-player list or map already has a record for the target, the source's record replaces it (the
target's own is dropped and reported). With `--keep-target-records` the target's record is kept and the source's is
left unchanged.

With `--from-name` and `--to-name`, name fields (`Name`, `Title`, `Owner`, `PlayerName`...) that equal the old name
are renamed, but only inside a compound where a UUID was also rewritten, so unrelated text is never touched.

## Safety

* Run with the game and server closed. On Windows, a world whose `session.lock` is held is refused.
* `--dry-run` lists every change (file, NBT path, what) and writes nothing.
* Each file is backed up before its first change, to `--backup-dir` (default: `<world>.backup-<time>` next to the
  world), keeping the folder layout, so restoring is a copy back.
* NBT is parsed into a form that writes back byte for byte; a file that wouldn't round-trip exactly is refused,
  so no data can be dropped silently. Region chunks are rewritten in place when they fit, else appended.
* The source's files are never deleted.

### The all-zero UUID

Some offline launchers and old worlds give a player the UUID `00000000-0000-0000-0000-000000000000`. Many mods also
use it for "nobody" (unowned machines, public things). So with it as the source:

* `--scope player` works as usual;
* `data` needs `--allow-nil`: read the dry-run list and check every entry is really that player's;
* `regions` also needs `--only-ids`, a regex of the block entity or entity ids that the player owned, for example
  `--only-ids "bonsai|petrock"`.

## Example

A 1.12.2 singleplayer world played in 2019 under an offline launcher (player "Alex", zero UUID), now played on an
account:

```bash
mcpack player-transfer saves/MyWorld --from 00000000-0000-0000-0000-000000000000 --to <account uuid> \
       --from-name Alex --to-name NewName --allow-nil --only-ids "bonsai|petrock" --dry-run
```

The dry run on such a world listed: the player record and host record, stats and advancements, a Cyclic extra
inventory file, Actually Additions player data, Extra Utilities 2 power frequency and login records, FTB Lib team
title and icon, FTB Lib's per-player file, FTB Quests claimed rewards, Chance Cubes profile settings, and the owners
of three bonsai pots and a pet rock.
