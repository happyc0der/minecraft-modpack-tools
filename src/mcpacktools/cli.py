"""The ``mcpack`` command."""

from __future__ import annotations

import argparse
import sys

from mcpacktools import __version__, itematlas, xbrz_build
from mcpacktools.textures import animated, atlasfit, upscale
from mcpacktools.world import finder, players

COMMANDS = (upscale, animated, atlasfit, itematlas, players, finder, xbrz_build)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcpack",
        description="Tools for Minecraft modpacks: HD texture packs, atlases, worlds and player data. "
                    "Run 'mcpack <command> --help' for details.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)
    for module in COMMANDS:
        module.add_parser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    return args.run(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
