"""Command-line tool for cliloc binary ↔ CSV conversion.

Usage after ``pip install -e .``::

    ultima-cliloc to-csv   cliloc.enu  cliloc.csv
    ultima-cliloc from-csv cliloc.csv  cliloc.custom1

Without installing::

    python -m ultima_sdk.cliloc_cli to-csv cliloc.enu cliloc.csv
"""

from __future__ import annotations

import argparse
import sys

from ultima_sdk.cliloc import Cliloc
from ultima_sdk.exceptions import UltimaSdkException


def _cmd_to_csv(args: argparse.Namespace) -> int:
    count = Cliloc.convert_to_csv(args.input, args.output)
    print(f"Wrote {count} entries to {args.output}")
    return 0


def _cmd_from_csv(args: argparse.Namespace) -> int:
    entries = Cliloc.import_csv(args.input)
    Cliloc.save_file(
        args.output,
        entries,
        header=args.header,
        version=args.version,
        unknown=args.unknown,
    )
    print(f"Wrote {len(entries)} entries to {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ultima-cliloc",
        description="Convert Ultima Online cliloc files between binary and CSV.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    to_csv = sub.add_parser(
        "to-csv",
        help="Export a cliloc binary file to CSV (columns: number, flag, text).",
    )
    to_csv.add_argument("input", help="Input cliloc file (e.g. cliloc.enu)")
    to_csv.add_argument("output", help="Output CSV file path")
    to_csv.set_defaults(func=_cmd_to_csv)

    from_csv = sub.add_parser(
        "from-csv",
        help="Build a cliloc binary file from a CSV.",
    )
    from_csv.add_argument("input", help="Input CSV file path")
    from_csv.add_argument("output", help="Output cliloc file path")
    from_csv.add_argument(
        "--header",
        choices=("standard", "v4", "none"),
        default="standard",
        help="Cliloc header style (default: standard = 6-byte header).",
    )
    from_csv.add_argument(
        "--version",
        type=int,
        default=2,
        help="Header version int32 (default: 2).",
    )
    from_csv.add_argument(
        "--unknown",
        type=int,
        default=0,
        help="Header int16 field for standard header (default: 0).",
    )
    from_csv.set_defaults(func=_cmd_from_csv)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except UltimaSdkException as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
