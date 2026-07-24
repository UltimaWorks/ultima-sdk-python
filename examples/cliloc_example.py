"""Cliloc example – load and query Ultima Online localization string tables.

Reads a cliloc file (e.g. ``cliloc.enu``) and demonstrates the full API of
:class:`~ultima_sdk.cliloc.Cliloc`.

Usage::

    python -m examples.cliloc_example [--uo-root /path/to/uo] [--id 3000001]
    python -m examples.cliloc_example --path /path/to/cliloc.enu
"""

from __future__ import annotations

import argparse

from ultima_sdk.cliloc import Cliloc

from ._common import add_uo_root_arg, init_files, resolve_uo_root

# A handful of well-known cliloc numbers to showcase on any UO install.
_WELL_KNOWN: list[int] = [
    3000001,   # "Cancel"
    3000432,   # "Blacksmith"
    1019548,   # "You have died."
    1060459,   # "You are dead."
    1011428,   # "OK"
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_uo_root_arg(parser)
    parser.add_argument(
        "--path",
        default=None,
        help="Explicit path to a cliloc file (skips auto-discovery).",
    )
    parser.add_argument(
        "--lang",
        default="enu",
        metavar="LANG",
        help="Language suffix for auto-discovery, e.g. 'enu' or 'deu' (default: enu).",
    )
    parser.add_argument(
        "--id",
        type=int,
        default=None,
        metavar="NUMBER",
        help="Print this specific entry number and the four following it.",
    )
    args = parser.parse_args()

    if args.path is None:
        init_files(
            resolve_uo_root(args.uo_root),
            require=True,
            require_any=(
                "cliloc.enu",
                "cliloc.deu",
                "cliloc.custom1",
                "cliloc.custom2",
            ),
        )

    if not Cliloc.initialize(path=args.path, language=args.lang):
        print("No cliloc file found. Provide --path or set --uo-root / UO_ROOT.")
        return 1

    print(f"Loaded {Cliloc.count()} entries.\n")

    # --- look up specific numbers ---
    if args.id is not None:
        print(f"Entries starting at {args.id}:")
        for num in range(args.id, args.id + 5):
            text = Cliloc.get_string(num)
            print(f"  [{num:>10}] {text!r}")
        print()

    # --- well-known entries (skip those not present) ---
    print("Well-known entries:")
    found_any = False
    for num in _WELL_KNOWN:
        if Cliloc.contains(num):
            print(f"  [{num:>10}] {Cliloc.get_string(num)!r}")
            found_any = True
    if not found_any:
        print("  (none of the well-known IDs were present in this file)")
    print()

    # --- first few entries sorted by number ---
    print("First 10 entries (sorted by number):")
    for num, text in sorted(Cliloc.all_entries().items())[:10]:
        print(f"  [{num:>10}] {text!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
