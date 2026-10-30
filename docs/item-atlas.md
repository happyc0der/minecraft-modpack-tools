# Purple/black items in 26.x resource packs

Minecraft 26.x bakes every item model against a single texture atlas. Resource packs made for older versions, and
plugin packs (Magic, ItemsAdder exports, Slimefun packs...) that put item textures anywhere, break in three ways the
game only reports in its log:

| Log message | Result in game |
|---|---|
| `Unable to bake item model: '<item>' ... Multiple atlases used in model` | the whole item is purple/black |
| `Missing textures in model <model>: <texture>` | those faces are purple/black |
| `Rejecting block model <model> ... sprites from outside of supported atlas` | the block renders as missing |

## How the game decides

* The **items atlas** holds `textures/item/**` of every namespace, plus every `single` and `paletted_permutations`
  source listed in `assets/minecraft/atlases/items.json` (the game's file and every pack's file are merged).
* The **blocks atlas** holds `textures/block/**` and its own listed sources.
* A model texture resolves to the items atlas if it's in it, otherwise to the blocks atlas, otherwise it is missing.
* Only textures the model's quads actually use count: the textures on element faces, or `layer0..N` for models
  whose parent chain ends in `builtin/generated`.
* An item model whose textures resolve to both atlases fails to bake. A block model that uses an items-atlas
  texture is rejected.
* A `single` source with a `"sprite"` field registers under that alias. Packs use `_namespace:path` aliases on
  purpose; they are not typos.
* Overlay directories in `pack.mcmeta` replace base files when the game's pack format is in their range.

`mcpack item-atlas --check` simulates all of this for every item definition (`assets/*/items/*.json`) and every
blockstate in the pack and the game, and reports the counts. On the packs it was developed with, the simulator's
lists matched the client log exactly.

## What the fix does

Repeated until nothing changes:

1. Item definitions pointing at models that don't exist (old `item/<name>` layouts) are pointed at the
   `ns:item/x` or `ns:block/x` model that does exist, or at the game's own definition for that item.
2. `_ns:path` references that resolve to nothing become `ns:path` when that file exists; faces that use an undefined
   texture variable get the model's first texture; models without a particle texture get their first one.
3. Atlas sources for files that exist nowhere are removed (they only cause `Missing sprite` spam).
4. Block-model textures in neither atlas are added to the blocks atlas.
5. Item-model textures in neither atlas are added to the items atlas, unless a block model uses them too.
6. Item models that still mix atlases get an items-atlas copy of each texture that may not move (the game's own
   textures, or ones block models use): `<copy-namespace>:item/<ns>/<path>`, and the model's texture variable
   points at the copy.

The game's own (`minecraft:`) textures are never added to the items atlas: their block models would be rejected,
which breaks crafting tables, glass panes and anvils.
