"""``mcpack player-transfer``: move one player's data onto another player (UUID) inside a world.

Typical uses: an offline/cracked name moving to a real account; joining an old singleplayer world that was played
under another name; an offline server after a rename; merging an old character into your current one.

What moves (each part can be limited with ``--scope``):

``player``   playerdata/<from>.dat becomes playerdata/<to>.dat (inventory, ender chest, XP, position, effects, mod
             data), with the UUID inside rewritten. The target's current inventory is kept in free slots unless
             ``--replace-target-items``. In singleplayer worlds, level.dat's ``Data.Player`` (the host record, which
             the game uses instead of playerdata) is replaced too when it belongs to either player, or with ``--host``.
             stats/ and advancements/ are merged (stats summed, advancements combined). Other files named after the
             source UUID (mod player files such as ``playerdata/<uuid>.baub``) are copied to the target's name.
``data``     UUID references in data/ (mod world data, NBT and JSON): teams, power networks, claims, profiles...
``regions``  UUID references in region/ and entities/ chunks: owners of machines, tamed animals, pets...

UUIDs are recognised in every common encoding (see :mod:`mcpacktools.world.uuids`). Records that the target already
has in a per-player list or map are replaced by the source's (``--keep-target-records`` does the opposite).

Safety: run with the game and server closed. ``--dry-run`` lists every change and writes nothing. Every file is backed
up before it is changed (``--backup-dir``, default ``<world>.backup-<time>`` next to the world). NBT is only edited
if it round-trips byte for byte. Source files are never deleted. The all-zero UUID (used by some offline setups)
also means "nobody" to many mods, so for it ``data``/``regions`` need ``--allow-nil``, and region changes need
``--only-ids`` (a regex of the block entity / entity ids to touch).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import time
from pathlib import Path
from uuid import UUID

from mcpacktools import nbt
from mcpacktools.nbt import BYTE, COMPOUND, INT_ARRAY, LIST, LONG, Compound, RegionFile, UnsupportedChunk
from mcpacktools.world.uuids import NIL, UuidRewriter, from_longs, parse_uuid, search_tokens, to_ints, to_longs

REGION_DIRS = {"region", "entities"}
MAIN_SLOTS = list(range(9, 36)) + list(range(0, 9))


def add_parser(sub) -> None:
    p = sub.add_parser("player-transfer", help="move a player's data onto another UUID in a world",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("world", type=Path, help="world folder (the one with level.dat)")
    p.add_argument("--from", dest="src", required=True, help="source UUID, or offline:<name>")
    p.add_argument("--to", dest="dst", required=True, help="target UUID, or offline:<name>")
    p.add_argument("--from-name", help="source player name (renamed to --to-name where it sits next to the UUID)")
    p.add_argument("--to-name", help="target player name")
    p.add_argument("--scope", default="player,data,regions", help="comma list of: player, data, regions")
    p.add_argument("--host", action="store_true", help="always write the result into level.dat's host record")
    p.add_argument("--replace-target-items", action="store_true", help="don't keep the target's current inventory")
    p.add_argument("--keep-target-records", action="store_true", help="where both have a record, keep the target's")
    p.add_argument("--only-ids", help="regex: only rewrite inside block entities/entities whose id matches (regions)")
    p.add_argument("--allow-nil", action="store_true", help="allow the all-zero UUID as source for data/regions")
    p.add_argument("--backup-dir", type=Path, help="where to back up changed files")
    p.add_argument("--dry-run", action="store_true", help="list every change; write nothing")
    p.set_defaults(run=run)


class Writer:
    """Writes files atomically, backing up each original once first (or only records the change on a dry run)."""

    def __init__(self, world: Path, backup_dir: Path, dry_run: bool) -> None:
        self.world, self.backup_dir, self.dry_run = world, backup_dir, dry_run
        self.written: list[str] = []

    def write(self, path: Path, data: bytes) -> None:
        rel = path.relative_to(self.world)
        self.written.append(str(rel).replace("\\", "/"))
        if self.dry_run:
            return
        backup = self.backup_dir / rel
        if path.exists() and not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)


def world_is_open(world: Path) -> bool:
    """True if the game holds the world's session.lock (detectable on Windows, where the lock is mandatory)."""
    lock = world / "session.lock"
    if not lock.exists():
        return False
    try:
        with open(lock, "rb") as f:
            f.read(1)
        return False
    except PermissionError:
        return True


# --------------------------------------------------------------------------------------------- player record


def record_uuid(player: Compound) -> UUID | None:
    if player.type_of("UUIDMost") == LONG and player.type_of("UUIDLeast") == LONG:
        return from_longs(player.get("UUIDMost"), player.get("UUIDLeast"))
    v = player.get("UUID")
    if player.type_of("UUID") == INT_ARRAY and len(v) == 4:
        return UUID(int=sum((x & 0xFFFFFFFF) << (96 - 32 * i) for i, x in enumerate(v)))
    return None


def set_record_uuid(player: Compound, u: UUID) -> None:
    if player.type_of("UUID") == INT_ARRAY:
        player.set("UUID", INT_ARRAY, to_ints(u))
    else:
        most, least = to_longs(u)
        player.set("UUIDMost", LONG, most)
        player.set("UUIDLeast", LONG, least)


def merge_inventory(new: Compound, target: Compound) -> list[str]:
    """Add the target's stacks to ``new``: same slot if free, else a free main-inventory slot. Returns moves made."""
    inv = new.get("Inventory")
    theirs = target.get("Inventory")
    if not isinstance(inv, nbt.ListTag) or not isinstance(theirs, nbt.ListTag) or not theirs.items:
        return []
    if inv.elem_type != COMPOUND:
        inv.elem_type = COMPOUND
    used = {item.get("Slot") for item in inv.items}
    free = [s for s in MAIN_SLOTS if s not in used]
    notes = []
    for item in theirs.items:
        item = copy.deepcopy(item)
        slot = item.get("Slot")
        if slot in used:
            if not free:
                raise SystemExit("the target's items don't fit in the source's free inventory slots; "
                                 "empty some slots in game or use --replace-target-items")
            slot = free.pop(0)
            item.set("Slot", item.type_of("Slot") or BYTE, slot)
        elif slot in free:
            free.remove(slot)
        used.add(slot)
        inv.items.append(item)
        ident = item.get("id")
        notes.append(f"{ident.decode() if isinstance(ident, bytes) else ident} -> slot {slot}")
    return notes


def merge_json_file(kind: str, a: dict, b: dict) -> dict:
    """Combine the source's (a) and the target's (b) stats or advancements."""
    out = copy.deepcopy(a)
    if kind == "advancements":
        for key, value in b.items():
            if key not in out or not isinstance(value, dict) or not isinstance(out[key], dict):
                out.setdefault(key, value)
                continue
            crit = out[key].setdefault("criteria", {})
            crit.update({c: d for c, d in value.get("criteria", {}).items() if c not in crit})
            out[key]["done"] = bool(out[key].get("done")) or bool(value.get("done"))
        return out

    def add(x: dict, y: dict) -> None:
        for key, value in y.items():
            if key == "DataVersion":
                x[key] = max(x.get(key, 0), value)
            elif isinstance(value, (int, float)) and isinstance(x.get(key), (int, float)):
                x[key] += value
            elif isinstance(value, dict) and isinstance(x.get(key), dict):
                add(x[key], value)
            elif key not in x:
                x[key] = value
    add(out, b)
    return out


def transfer_player(world: Path, src: UUID, dst: UUID, args, out: Writer, log: list[str], data_scope: bool) -> None:
    pdir = world / "playerdata"
    src_file = pdir / f"{src}.dat"
    if not src_file.exists():
        raise SystemExit(f"no player record for {src}: {src_file} does not exist")
    root, gz = nbt.read_file(src_file)
    new = copy.deepcopy(root)
    set_record_uuid(new.value, dst)
    if src != NIL:  # mod data inside the player record may name the player too
        rw = UuidRewriter(src, dst, src_name=args.from_name, dst_name=args.to_name)
        rw.compound(new.value, "player")
    level_file = world / "level.dat"
    level, level_gz = nbt.read_file(level_file) if level_file.exists() else (None, False)
    host = level.value.get("Data").get("Player") if level else None
    host_uuid = record_uuid(host) if isinstance(host, Compound) else None

    target = None
    dst_file = pdir / f"{dst}.dat"
    if dst_file.exists():
        target = nbt.read_file(dst_file)[0].value
    elif host is not None and host_uuid == dst:
        target = host
    if target is not None and not args.replace_target_items:
        moves = merge_inventory(new.value, target)
        if moves:
            log.append(f"kept the target's {len(moves)} stacks: {', '.join(moves)}")
    out.write(dst_file, nbt.encode_file(new, gz))
    log.append(f"playerdata/{dst}.dat <- playerdata/{src}.dat")

    if host is not None and (args.host or host_uuid in (src, dst)):
        level.value.get("Data").set("Player", COMPOUND, copy.deepcopy(new.value))
        out.write(level_file, nbt.encode_file(level, level_gz))
        log.append("level.dat host record (Data.Player) replaced")
    elif host is not None:
        log.append(f"level.dat host record belongs to {host_uuid}; left alone (use --host to replace it)")

    for kind in ("stats", "advancements"):
        a, b = world / kind / f"{src}.json", world / kind / f"{dst}.json"
        if not a.exists():
            continue
        src_json = json.loads(a.read_text(encoding="utf-8"))
        merged = merge_json_file(kind, src_json, json.loads(b.read_text(encoding="utf-8"))) if b.exists() else src_json
        out.write(b, json.dumps(merged, indent=2 if kind == "advancements" else None).encode("utf-8"))
        log.append(f"{kind}/{dst}.json <- merged with the source's")

    handled = {src_file, pdir / f"{src}.dat_old", world / "stats" / f"{src}.json", world / "advancements" / f"{src}.json"}
    skip_dirs = REGION_DIRS | ({"data"} if data_scope else set())  # data/ files are rewritten, not just copied
    for path in sorted(world.rglob(f"*{src}*")):
        if path.is_file() and path not in handled and not skip_dirs & set(path.relative_to(world).parts[:-1]):
            dest = path.with_name(path.name.replace(str(src), str(dst)))
            out.write(dest, path.read_bytes())
            log.append(f"{dest.relative_to(world).as_posix()} <- {path.name}")


# --------------------------------------------------------------------------------------------- world references


def contains_any(data: bytes, tokens: list[bytes]) -> bool:
    return any(t in data for t in tokens)


def target_path(path: Path, src: UUID, dst: UUID, names: tuple[str, str] | None) -> Path:
    """Where the rewritten copy of a data file goes: files named after the source player get the target's name
    (by UUID, or by player name like FTB Lib's ``players/<name>.dat``); the source's own file is left as it was."""
    name = path.name
    for old, new in ((str(src), str(dst)), (src.hex, dst.hex)):
        name = re.sub(re.escape(old), new, name, flags=re.IGNORECASE)
    if names and path.stem.lower() == names[0].lower():
        stem = names[1].lower() if path.stem.islower() else names[1]
        name = stem + path.suffix
    return path.with_name(name)


def rewrite_data(world: Path, rw: UuidRewriter, tokens: list[bytes], out: Writer, log: list[str],
                 names: tuple[str, str] | None = None) -> None:
    for path in sorted(world.rglob("*")):
        rel = path.relative_to(world)
        if not path.is_file() or "data" not in rel.parts[:-1] or REGION_DIRS & set(rel.parts):
            continue
        dest = target_path(path, rw.src, rw.dst, names)
        if dest != path and dest.exists() and rw.keep_target_records:
            log.append(f"skipped {rel.as_posix()}: the target already has {dest.name}")
            continue
        before = len(rw.hits)
        if path.suffix == ".dat":
            try:
                root, gz = nbt.read_file(path)
            except (ValueError, OSError) as e:
                log.append(f"skipped {rel.as_posix()}: {e}")
                continue
            if root.type != COMPOUND or (tokens and not contains_any(nbt.dumps(root), tokens)):
                continue
            rw.compound(root.value, rel.as_posix())
            if len(rw.hits) > before:
                out.write(dest, nbt.encode_file(root, gz))
        elif path.suffix == ".json":
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
            if tokens and not contains_any(text.encode("utf-8", "surrogateescape"), tokens):
                continue
            try:
                data = json.loads(text)
            except ValueError:
                continue
            new = rw.json(data, rel.as_posix())
            if len(rw.hits) > before:
                out.write(dest, json.dumps(new, separators=(",", ":")).encode("utf-8"))
        if len(rw.hits) > before:
            moved = "" if dest == path else f" -> written as {dest.relative_to(world).as_posix()}"
            log.append(f"{rel.as_posix()}: {len(rw.hits) - before} change(s){moved}")


def rewrite_regions(world: Path, rw: UuidRewriter, tokens: list[bytes], out: Writer, log: list[str]) -> None:
    for path in sorted(world.rglob("*.mca")):
        rel = path.relative_to(world)
        if rel.parent.name not in REGION_DIRS:
            continue
        region = RegionFile(path)
        changed_chunks = 0
        for slot in region.slots():
            try:
                raw, scheme = region.read(slot)
            except UnsupportedChunk as e:
                log.append(f"skipped {rel.as_posix()}: {e}")
                continue
            if tokens and not contains_any(raw, tokens):
                continue
            root = nbt.loads(raw)
            if nbt.dumps(root) != raw:
                log.append(f"skipped {rel.as_posix()} chunk {slot}: NBT does not round-trip exactly")
                continue
            before = len(rw.hits)
            rw.compound(root.value, f"{rel.as_posix()}#{slot}", allowed=rw.only_ids is None)
            if len(rw.hits) > before:
                region.write(slot, nbt.dumps(root), scheme)
                changed_chunks += 1
        if region.dirty:
            out.write(path, region.to_bytes())
            log.append(f"{rel.as_posix()}: {changed_chunks} chunk(s) changed")


# --------------------------------------------------------------------------------------------- command


def run(args) -> int:
    world = args.world.resolve()
    if not (world / "level.dat").exists() and not (world / "playerdata").is_dir():
        raise SystemExit(f"{world} doesn't look like a world folder (no level.dat or playerdata/)")
    if world_is_open(world):
        raise SystemExit("the world is open in the game or a server (session.lock is held); close it first")
    src, dst = parse_uuid(args.src), parse_uuid(args.dst)
    if src == dst:
        raise SystemExit("--from and --to are the same UUID")
    scope = {s.strip() for s in args.scope.split(",") if s.strip()}
    if src == NIL and scope & {"data", "regions"} and not args.allow_nil:
        raise SystemExit("the source is the all-zero UUID, which many mods use for 'nobody'. Run with --scope player, "
                         "or add --allow-nil (and --only-ids for regions) after checking --dry-run")
    if src == NIL and "regions" in scope and not args.only_ids:
        raise SystemExit("with the all-zero UUID, region changes need --only-ids")
    backup = args.backup_dir or world.with_name(f"{world.name}.backup-{time.strftime('%Y%m%d-%H%M%S')}")
    out = Writer(world, backup, args.dry_run)
    log: list[str] = []
    rw = UuidRewriter(src, dst, src_name=args.from_name, dst_name=args.to_name,
                      keep_target_records=args.keep_target_records,
                      only_ids=re.compile(args.only_ids) if args.only_ids else None)
    tokens = [] if src == NIL else search_tokens(src)

    if "player" in scope:
        transfer_player(world, src, dst, args, out, log, data_scope="data" in scope)
    if "data" in scope:
        names = (args.from_name, args.to_name) if args.from_name and args.to_name else None
        rewrite_data(world, rw, tokens, out, log, names)
    if "regions" in scope:
        rewrite_regions(world, rw, tokens, out, log)

    print(("DRY RUN, nothing written. " if args.dry_run else "") + f"{src} -> {dst} in {world.name}")
    for line in log:
        print("  " + line)
    if rw.hits:
        print(f"UUID references rewritten: {len(rw.hits)}")
        for hit in rw.hits[:200]:
            print("    " + hit)
        if len(rw.hits) > 200:
            print(f"    ... and {len(rw.hits) - 200} more")
    if rw.notes:
        print(f"left alone ({len(rw.notes)}):")
        for note in rw.notes:
            print("    " + note)
    print(f"files {'to change' if args.dry_run else 'changed'}: {len(set(out.written))}"
          + ("" if args.dry_run or not out.written else f"; originals backed up in {backup}"))
    return 0
