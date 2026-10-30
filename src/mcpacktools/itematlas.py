"""``mcpack item-atlas``: find and fix purple/black items in resource packs for Minecraft 26.x.

Since the 26.x item rendering changes, item models are baked against ONE texture atlas. Packs written for older
versions (and many plugin packs) break in ways the game only reports in its log:

* ``Unable to bake item model ... Multiple atlases used in model``: the item mixes textures from the items atlas and
  the blocks atlas, and the whole item renders purple/black;
* ``Missing textures in model``: a texture is in neither atlas, so that part renders purple/black;
* ``Rejecting block model ... sprites from outside of supported atlas``: a block model uses an items-atlas texture.

The rules (checked against the client log, which this simulator reproduced exactly on two real packs):

* items atlas = ``textures/item/**`` of every namespace + the ``single``/``paletted_permutations`` sources in
  ``atlases/items.json`` (game + pack merged); blocks atlas = ``textures/block/**`` + its own sources;
* a model texture resolves to the items atlas if it is in it, else to the blocks atlas, else it is missing;
* only textures the model's quads use count (element faces, or ``layerN`` for ``item/generated``);
* single sources with a ``"sprite"`` field register under that alias (``_ns:path`` refs are often such aliases).

``--check`` (or no output path) reports only. With an output path it fixes, repeating until nothing changes:

1. item definitions pointing at models that don't exist (old ``item/x`` layouts) -> the right ``ns:item/x`` or
   ``ns:block/x`` model, or the game's own definition for that item;
2. ``_ns:path`` refs that resolve to nothing -> ``ns:path`` when that file exists; faces using an undefined texture
   variable -> the model's first texture; models without a particle texture get their first texture;
3. atlas sources for files that exist nowhere are removed ("Missing sprite" log spam);
4. block-model textures in neither atlas -> added to the blocks atlas;
5. item-model textures in neither atlas -> added to the items atlas, unless a block model also uses them;
6. item models still mixing atlases -> each texture that may not move (game textures, or ones block models use) is
   copied to ``<copy-namespace>:item/<ns>/<path>`` and the model points at the copy.

Game (``minecraft:``) textures are never added to the items atlas: that would break the game's own block models.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import zipfile
from pathlib import Path

MISSING = "minecraft:missingno"
ITEMS_JSON = "assets/minecraft/atlases/items.json"
BLOCKS_JSON = "assets/minecraft/atlases/blocks.json"


def add_parser(sub) -> None:
    p = sub.add_parser("item-atlas", help="find/fix purple-black items in 26.x resource packs",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pack", type=Path, help="resource pack zip")
    p.add_argument("out", type=Path, nargs="?", help="fixed pack to write (omit to only report)")
    p.add_argument("--client", type=Path, required=True, help="the game's client jar (for its models and atlases)")
    p.add_argument("--pack-format", type=int, help="resource pack format of the game (default: read from the jar)")
    p.add_argument("--copy-namespace", default="atlasfix", help="namespace for item-atlas copies (default atlasfix)")
    p.add_argument("--check", action="store_true", help="report only")
    p.add_argument("--list", action="store_true", help="with --check: list every model with missing textures")
    p.set_defaults(run=run)


def split(ref: str) -> tuple[str, str]:
    return tuple(ref.split(":", 1)) if ":" in ref else ("minecraft", ref)


def tid(ns: str, path: str) -> str:
    return f"{ns}:{path}"


def client_pack_format(jar: zipfile.ZipFile) -> int:
    version = json.loads(jar.read("version.json"))
    pv = version.get("pack_version")
    return pv.get("resource_major", pv.get("resource")) if isinstance(pv, dict) else int(pv)


class PackView:
    """The resource pack layered over the game jar, as the game sees it, plus pending edits."""

    def __init__(self, pack_zip: Path, client_jar: Path, pack_format: int | None) -> None:
        self.zp = zipfile.ZipFile(pack_zip)
        self.zv = zipfile.ZipFile(client_jar)
        self.pack_format = pack_format or client_pack_format(self.zv)
        self.pack_names = set(self.zp.namelist())
        self.game_names = set(self.zv.namelist())
        self.edits: dict[str, bytes] = {}
        self.overlay: dict[str, str] = self._active_overlays()
        self.pack_names |= set(self.overlay)
        self._used_cache: dict[str, dict] = {}

    def _active_overlays(self) -> dict[str, str]:
        """Overlay directories active for this pack format: base path -> overlay path."""
        try:
            meta = json.loads(self.zp.read("pack.mcmeta").decode("utf-8-sig"))
        except (KeyError, ValueError):
            return {}

        def bound(entry, key, index, default):
            v = entry.get(key)
            if v is None and isinstance(entry.get("formats"), list) and entry["formats"]:
                v = entry["formats"][index]
            if isinstance(v, list):
                v = v[0]
            return default if v is None else v

        out = {}
        for entry in (meta.get("overlays") or {}).get("entries", []):
            if bound(entry, "min_format", 0, 0) <= self.pack_format <= bound(entry, "max_format", -1, 10 ** 6):
                prefix = entry["directory"].strip("/") + "/"
                for name in self.zp.namelist():
                    if name.startswith(prefix) and not name.endswith("/"):
                        out[name[len(prefix):]] = name
        return out

    # files
    def has(self, path: str) -> bool:
        return path in self.edits or path in self.pack_names or path in self.game_names

    def in_pack(self, path: str) -> bool:
        return path in self.edits or path in self.pack_names

    def read(self, path: str) -> bytes:
        if path in self.edits:
            return self.edits[path]
        if path in self.overlay:
            return self.zp.read(self.overlay[path])
        return self.zp.read(path) if path in self.pack_names else self.zv.read(path)

    def json(self, path: str):
        return json.loads(self.read(path).decode("utf-8-sig"))

    def write_json(self, path: str, data, indent=1) -> None:
        self.edits[path] = json.dumps(data, indent=indent).encode()

    # atlases
    def _sources(self, atlas: str) -> list:
        path = f"assets/minecraft/atlases/{atlas}.json"
        out = []
        if path in self.game_names:
            out += json.loads(self.zv.read(path).decode("utf-8-sig")).get("sources", [])
        if self.in_pack(path):
            out += self.json(path).get("sources", [])
        return out

    def _atlas(self, atlas: str):
        dirs, singles, paletted = [], set(), set()
        for s in self._sources(atlas):
            kind = str(s.get("type", "")).replace("minecraft:", "")
            if kind == "directory":
                dirs.append(s.get("source", "").strip("/") + "/")
            elif kind == "single":
                singles.add(tid(*split(s.get("sprite") or s["resource"])))
            elif kind == "paletted_permutations":
                for tex in s.get("textures", []):
                    ns, p = split(tex)
                    paletted |= {tid(ns, f"{p}_{perm}") for perm in s.get("permutations", {})}
        return dirs, singles | paletted, paletted

    def reload(self) -> None:
        self.item_dirs, self.item_singles, pal_i = self._atlas("items")
        self.block_dirs, self.block_singles, pal_b = self._atlas("blocks")
        self.paletted = pal_i | pal_b
        self.reset_cache()

    def reset_cache(self) -> None:
        self._used_cache.clear()

    def in_items(self, t: str) -> bool:
        return t in self.item_singles or any(split(t)[1].startswith(d) for d in self.item_dirs)

    def in_blocks(self, t: str) -> bool:
        return t in self.block_singles or any(split(t)[1].startswith(d) for d in self.block_dirs)

    def texture_exists(self, t: str) -> bool:
        ns, p = split(t)
        return self.has(f"assets/{ns}/textures/{p}.png") or t in self.paletted

    def state(self, t: str) -> str:
        """'items' | 'blocks' | 'none' (file exists, in neither atlas) | 'nofile'."""
        if t == MISSING:
            return "nofile"
        if self.in_items(t):
            return "items"
        if self.in_blocks(t):
            return "blocks"
        return "none" if self.texture_exists(t) else "nofile"

    # models
    def chain(self, ref: str) -> list[tuple[str | None, dict]]:
        """The model and its parents, child first; a builtin parent appears as {"__builtin__": name}."""
        out, cur = [], ref
        for _ in range(30):
            ns, p = split(cur)
            path = f"assets/{ns}/models/{p}.json"
            if not self.has(path):
                break
            try:
                model = self.json(path)
            except ValueError:
                model = {}
            out.append((path, model))
            parent = model.get("parent")
            if not isinstance(parent, str):
                break
            if "builtin/" in parent:
                out.append((None, {"__builtin__": parent.split("/")[-1]}))
                break
            cur = parent
        return out

    def used(self, ref: str) -> dict[str, tuple[str | None, str]]:
        """{face ref: (final texture variable, texture id)} for the textures the model's quads use."""
        key = tid(*split(ref))
        if key in self._used_cache:
            return self._used_cache[key]
        ch = self.chain(ref)
        textures = {}
        for _, model in reversed(ch):
            for k, v in (model.get("textures") or {}).items():
                textures[k] = v.get("sprite", "") if isinstance(v, dict) else v
        elements = next((m["elements"] for _, m in ch if isinstance(m.get("elements"), list)), None)
        if elements is not None:
            refs = {face["texture"] if face["texture"].startswith("#") else "#" + face["texture"]
                    for el in elements if isinstance(el, dict)
                    for face in (el.get("faces") or {}).values()
                    if isinstance(face, dict) and isinstance(face.get("texture"), str)}
        elif any(m.get("__builtin__") == "generated" for _, m in ch):
            refs = {f"#layer{i}" for i in range(16) if f"layer{i}" in textures}
        else:
            refs = set()
        out = {}
        for ref_ in refs:
            value, var, seen = ref_, None, set()
            while isinstance(value, str) and value.startswith("#"):
                name = value[1:]
                if value in seen or name not in textures:
                    var, value = name, None
                    break
                seen.add(value)
                var, value = name, textures[name]
            out[ref_] = (var, tid(*split(value)) if isinstance(value, str) and value and not value.startswith("#") else MISSING)
        self._used_cache[key] = out
        return out

    @staticmethod
    def model_refs(node, acc: set) -> set:
        """Every model referenced by an item definition or blockstate."""
        if isinstance(node, dict):
            kind = str(node.get("type", ""))
            if isinstance(node.get("model"), str) and (kind.endswith("model") or "type" not in node):
                acc.add(node["model"])
            if kind.endswith("special") and isinstance(node.get("base"), str):
                acc.add(node["base"])
            for v in node.values():
                PackView.model_refs(v, acc)
        elif isinstance(node, list):
            for v in node:
                PackView.model_refs(v, acc)
        return acc

    def item_definitions(self) -> dict[str, set]:
        out = {}
        for path in sorted(n for n in self.pack_names | self.game_names
                           if n.startswith("assets/") and n.count("/") == 3 and n.split("/")[2] == "items" and n.endswith(".json")):
            try:
                out[path] = self.model_refs(self.json(path).get("model", {}), set())
            except ValueError:
                continue
        return out

    def block_models(self) -> set:
        refs: set = set()
        for n in self.pack_names | self.game_names:
            if n.count("/") >= 3 and n.split("/")[2] == "blockstates" and n.endswith(".json"):
                try:
                    self.model_refs(self.json(n), refs)
                except ValueError:
                    pass
        return refs

    def mixes_atlases(self, ref: str) -> bool:
        return len({"items" if self.state(t) == "items" else "blocks" for _, t in self.used(ref).values()}) > 1

    def has_missing(self, ref: str) -> bool:
        return any(self.state(t) in ("none", "nofile") for _, t in self.used(ref).values())

    def needs_fix(self, ref: str) -> bool:
        """Mixes atlases, or uses a texture that exists but is in neither atlas (absent files can't be fixed here)."""
        return self.mixes_atlases(ref) or any(self.state(t) == "none" for _, t in self.used(ref).values())


Report = collections.namedtuple("Report", "failing missing_item no_model rejected missing_block")


def report(view: PackView, items: dict[str, set], blocks: set) -> Report:
    failing = {p: sorted(r for r in refs if view.mixes_atlases(r)) for p, refs in items.items()}
    return Report(
        failing={p: v for p, v in failing.items() if v},
        missing_item={r for refs in items.values() for r in refs if view.has_missing(r)},
        no_model={r for refs in items.values() for r in refs if not view.chain(r)},
        rejected={r for r in blocks if any(view.state(t) == "items" for _, t in view.used(r).values())},
        missing_block={r for r in blocks if view.has_missing(r)},
    )


def print_report(tag: str, r: Report) -> None:
    print(f"[{tag}] items failing to bake: {len(r.failing)} | item models with missing textures: {len(r.missing_item)} | "
          f"item models with no json: {len(r.no_model)} | block models rejected: {len(r.rejected)} | "
          f"block models with missing textures: {len(r.missing_block)}")


class Fixer:
    def __init__(self, view: PackView, copy_namespace: str) -> None:
        self.view = view
        self.copy_ns = copy_namespace
        self.stats = collections.Counter()
        v = view
        self.items_json = v.json(ITEMS_JSON) if v.in_pack(ITEMS_JSON) else {"sources": []}
        self.blocks_json = v.json(BLOCKS_JSON) if v.in_pack(BLOCKS_JSON) else {"sources": []}

    def commit(self) -> None:
        self.view.write_json(ITEMS_JSON, self.items_json)
        self.view.write_json(BLOCKS_JSON, self.blocks_json)
        self.view.reload()

    def fix_item_definitions(self) -> None:
        """Step 1: definitions pointing at models that don't exist."""
        v = self.view

        def resolve(ref):
            ns, p = split(ref)
            for cand in (f"item/{p}", "block/" + p[5:] if p.startswith("item/") else None):
                if cand and v.has(f"assets/{ns}/models/{cand}.json"):
                    return tid(ns, cand)
            return None

        def repair(node, game_root):
            if isinstance(node, list):
                return [repair(x, game_root) for x in node]
            if not isinstance(node, dict):
                return node
            kind = str(node.get("type", ""))
            key = ("model" if kind.endswith("model") and isinstance(node.get("model"), str) else
                   "base" if kind.endswith("special") and isinstance(node.get("base"), str) else None)
            if key and not v.chain(node[key]):
                new = resolve(node[key])
                if new:
                    self.stats["model refs fixed"] += 1
                    return {**node, key: new}
                if game_root is not None:
                    self.stats["model refs -> game definition"] += 1
                    return json.loads(json.dumps(game_root))
                self.stats["model refs unfixable"] += 1
            return {k: repair(x, game_root) for k, x in node.items()}

        for path, refs in v.item_definitions().items():
            if not v.in_pack(path) or all(v.chain(r) for r in refs):
                continue
            game_root = json.loads(v.zv.read(path)).get("model") if path in v.game_names else None
            data = v.json(path)
            data["model"] = repair(data.get("model", {}), game_root)
            v.edits[path] = json.dumps(data, separators=(",", ":")).encode()

    def drop_stale_sources(self) -> None:
        """Step 3: atlas single sources whose file exists nowhere."""
        def stale(src):
            if str(src.get("type", "")).replace("minecraft:", "") != "single":
                return False
            ns, p = split(src["resource"])
            return not self.view.has(f"assets/{ns}/textures/{p}.png")
        for atlas in (self.items_json, self.blocks_json):
            before = len(atlas["sources"])
            atlas["sources"] = [s for s in atlas["sources"] if not stale(s)]
            self.stats["stale atlas sources removed"] += before - len(atlas["sources"])

    def fix_model_refs(self, refs: set) -> None:
        """Step 2: underscore namespaces, undefined variables and missing particles in the pack's own models."""
        v = self.view
        for ref in refs:
            ch = v.chain(ref)
            if not ch or not ch[0][0] or not v.in_pack(ch[0][0]):
                continue
            path = ch[0][0]
            model = v.json(path)
            textures = model.setdefault("textures", {})
            changed = False
            for var, t in v.used(ref).values():
                if t == MISSING and var and var not in textures:
                    first = next((x for x in textures.values() if isinstance(x, str) and not x.startswith("#")), None)
                    if first:
                        textures[var] = first
                        changed = True
                        self.stats["undefined variables"] += 1
                    continue
                ns, p = split(t)
                if ns.startswith("_") and var and v.state(t) == "nofile" and v.has(f"assets/{ns[1:]}/textures/{p}.png"):
                    textures[var] = tid(ns[1:], p)
                    changed = True
                    self.stats["underscore namespaces"] += 1
            merged = {}
            for _, m in reversed(ch):
                merged.update(m.get("textures") or {})
            flat = any(m.get("__builtin__") for _, m in ch)  # item/generated etc. default the particle to layer0
            if merged and "particle" not in merged and not flat:
                first = next((k for k, x in merged.items() if isinstance(x, str) and not x.startswith("#")), None)
                if first:
                    textures["particle"] = "#" + first
                    changed = True
                    self.stats["particles added"] += 1
            if changed:
                v.write_json(path, model)

    def add(self, atlas: dict, t: str) -> None:
        atlas["sources"].append({"type": "minecraft:single", "resource": t})

    def copy_for_items(self, t: str) -> str:
        ns, p = split(t)
        for ext in (".png", ".png.mcmeta"):
            src = f"assets/{ns}/textures/{p}{ext}"
            if self.view.has(src):
                self.view.edits[f"assets/{self.copy_ns}/textures/item/{ns}/{p}{ext}"] = self.view.read(src)
        self.stats["textures copied for items"] += 1
        return f"{self.copy_ns}:item/{ns}/{p}"

    def fix_block_models(self, blocks: set) -> set:
        """Step 4; returns the textures block models use (those must never move to the items atlas)."""
        v = self.view
        for ref in blocks:
            for _, t in v.used(ref).values():
                if v.state(t) == "none" and split(t)[0] != "minecraft":
                    self.add(self.blocks_json, t)
                    v.block_singles.add(t)
                    self.stats["added to blocks atlas"] += 1
        self.commit()
        return {t for ref in blocks for _, t in v.used(ref).values()}

    def fix_item_models(self, item_refs: set, protected: set) -> None:
        """Steps 5 and 6, repeated until nothing changes."""
        v = self.view
        for _ in range(10):
            changed = False
            for ref in sorted(item_refs):
                if not v.needs_fix(ref):
                    continue
                for t in sorted({t for _, t in v.used(ref).values()}):
                    if (v.state(t) == "none" and split(t)[0] != "minecraft" and t not in protected
                            and not v.in_blocks(t) and t not in v.item_singles):
                        self.add(self.items_json, t)
                        v.item_singles.add(t)
                        self.stats["added to items atlas"] += 1
                        changed = True
                v.reset_cache()
                if not v.needs_fix(ref):
                    continue
                ch = v.chain(ref)
                path = ch[0][0] if ch else None
                if not path or not v.in_pack(path):
                    continue
                model = v.json(path)
                textures = model.setdefault("textures", {})
                for var, t in v.used(ref).values():
                    if v.state(t) in ("blocks", "none") and var:
                        textures[var] = self.copy_for_items(t)
                        self.stats["refs rewritten"] += 1
                        changed = True
                v.write_json(path, model)
            self.commit()
            if not changed:
                break


def write_pack(view: PackView, dest: Path) -> None:
    """Copy the pack with the edits applied; edits to overlaid files go into the overlay."""
    edits = {view.overlay.get(n, n): data for n, data in view.edits.items()}
    tmp = dest.with_name(dest.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        written = set()
        for info in view.zp.infolist():
            out.writestr(info, edits.get(info.filename, view.zp.read(info.filename)))
            written.add(info.filename)
        for name, data in edits.items():
            if name not in written:
                out.writestr(name, data)
    view.zp.close()
    os.replace(tmp, dest)


def run(args) -> int:
    view = PackView(args.pack, args.client, args.pack_format)
    view.reload()
    items, blocks = view.item_definitions(), view.block_models()
    print(f"pack format {view.pack_format}; item definitions: {len(items)}; block models: {len(blocks)}")
    before = report(view, items, blocks)
    print_report("before", before)
    if args.check or not args.out:
        for p in sorted(before.failing)[:20]:
            print("   fails:", p, before.failing[p][:2])
        if args.list:
            for r in sorted(before.missing_item):
                bad = sorted({f"{t}={view.state(t)}" for _, t in view.used(r).values() if view.state(t) in ("none", "nofile")})
                print("   missing texture:", r, bad[:4])
            for r in sorted(before.no_model):
                print("   no model json:", r)
        return 1 if before.failing or before.rejected else 0

    fixer = Fixer(view, args.copy_namespace)
    fixer.fix_item_definitions()
    items = view.item_definitions()
    fixer.drop_stale_sources()
    item_refs = set().union(*items.values()) if items else set()
    fixer.fix_model_refs(item_refs | blocks)
    fixer.commit()
    protected = fixer.fix_block_models(blocks)
    fixer.fix_item_models(item_refs, protected)

    print("changes:", dict(fixer.stats))
    after = report(view, items, blocks)
    print_report("after", after)
    for p in sorted(after.failing)[:10]:
        print("   still fails:", p, after.failing[p][:2])
    absent = sorted({t for r in after.missing_item | after.missing_block for _, t in view.used(r).values()
                     if view.state(t) == "nofile"})
    print(f"   textures referenced but in neither pack nor game: {len(absent)}", absent[:10])
    write_pack(view, args.out)
    print("wrote", args.out)
    return 0
