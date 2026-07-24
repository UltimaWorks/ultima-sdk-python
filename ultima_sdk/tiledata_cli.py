"""Command-line tool for tiledata – CSV-first workflow with binary preview."""

from __future__ import annotations

import argparse
import sys

from ultima_sdk.exceptions import UltimaSdkException
from ultima_sdk.tiledata import TileData, parse_flag_names_list


def _parse_int(value: str) -> int:
    return int(value, 0)


def _flatten_flag_args(values: list[str]) -> list[str]:
    flags: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                flags.append(part)
    return flags


def _add_tile_target(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    target = parser.add_mutually_exclusive_group(required=required)
    target.add_argument(
        "--item",
        metavar="ID",
        help=(
            "Item id: UO graphic (0xb0e5) or, in CSV, tiledata index when a "
            "row with matching id exists"
        ),
    )
    target.add_argument(
        "--index",
        metavar="ID",
        help="Tiledata item index (CSV id column); bypasses graphic-id decoding",
    )
    target.add_argument(
        "--land",
        metavar="ID",
        help="Land tile index (decimal or hex)",
    )


def _parse_tile_target(args: argparse.Namespace) -> tuple[int | None, int | None, bool]:
    if args.index is not None:
        return _parse_int(args.index), None, True
    item = _parse_int(args.item) if args.item is not None else None
    land = _parse_int(args.land) if args.land is not None else None
    return item, land, False


def _print_tile_info(info: dict[str, object], label: str) -> None:
    kind = info["kind"]
    tile_index = info["tile_index"]
    print(f"[{label}] {kind} index {tile_index}", end="")
    if info.get("graphic_id") is not None:
        print(f" (graphic {info['graphic_id']:#x})", end="")
    name = info.get("name")
    if name:
        print(f" [{name!r}]", end="")
    print()
    print(f"  flags: {info['flags_hex']}  {info['flags_names']}")
    if kind == "land":
        print(f"  texture_id: {info.get('texture_id')}")
    else:
        print(
            "  "
            f"weight={info.get('weight')} layer={info.get('layer')} "
            f"count={info.get('count')} height={info.get('height')}"
        )


def _print_patch_result(result: dict[str, object]) -> None:
    kind = result["kind"]
    tile_index = result["tile_index"]
    print(f"Patched {kind} tile index {tile_index}", end="")
    if result.get("graphic_id") is not None:
        print(f" (graphic {result['graphic_id']:#x})", end="")
    if result.get("name"):
        print(f" [{result['name']!r}]", end="")
    print()
    print(f"  old: {result['old_flags_hex']}  {result['old_flags_names']}")
    print(f"  new: {result['new_flags_hex']}  {result['new_flags_names']}")
    print(f"Saved to {result['output_path']}")


def _cmd_show(args: argparse.Namespace) -> int:
    item, land, use_index = _parse_tile_target(args)

    csv_info = TileData.get_tile_info(
        args.csv, item=item, land=land, use_index=use_index
    )
    _print_tile_info(csv_info, "csv")

    if args.vs:
        mul_info = TileData._get_tile_info_by_index(
            args.vs, str(csv_info["kind"]), int(csv_info["tile_index"])
        )
        print()
        _print_tile_info(mul_info, "mul")
        same = (
            csv_info["flags"] == mul_info["flags"]
            and csv_info.get("name") == mul_info.get("name")
        )
        print()
        print("Match:" if same else "Difference detected.", end=" ")
        if not same and csv_info["flags"] != mul_info["flags"]:
            print(
                f"flags csv={csv_info['flags_hex']} mul={mul_info['flags_hex']}"
            )
        elif not same:
            print(f"name csv={csv_info.get('name')!r} mul={mul_info.get('name')!r}")
        else:
            print("csv and mul agree.")
    return 0


def _cmd_set_flag(args: argparse.Namespace) -> int:
    item, land, use_index = _parse_tile_target(args)
    set_flags = parse_flag_names_list([args.set]) if args.set is not None else None

    result = TileData.patch_flags(
        args.csv,
        args.output,
        item=item,
        land=land,
        add=_flatten_flag_args(args.add),
        remove=_flatten_flag_args(args.remove),
        set_flags=set_flags,
        use_index=use_index,
    )
    _print_patch_result(result)
    return 0


def _cmd_pull(args: argparse.Namespace) -> int:
    count = TileData.convert_to_csv(args.mul, args.csv)
    print(f"Pulled {count} rows from {args.mul} -> {args.csv}")
    print("Review diff with: ultima-tiledata diff", args.csv, args.mul)
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    if args.new_format:
        new_format: bool | None = True
    elif getattr(args, "old_format", False):
        new_format = False
    else:
        new_format = None

    snapshot = TileData.import_csv(
        args.csv,
        new_format=new_format,
        static_group_count=args.static_groups,
        reference_mul_path=args.mul,
    )
    TileData.save_snapshot(args.mul, snapshot)
    count = len(snapshot.land_tiles) + len(snapshot.item_tiles)
    layout = "CV_7090+" if snapshot.new_format else "classic"
    print(
        f"Built {args.mul} from {args.csv} "
        f"({count} tile rows, {layout} format, "
        f"{snapshot.static_group_count} static groups)"
    )
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    item, land, use_index = _parse_tile_target(args)

    result = TileData.diff_csv_vs_mul(
        args.csv,
        args.mul,
        item=item,
        land=land,
        max_report=args.limit,
        use_index=use_index,
    )

    if item is not None or land is not None:
        tile = result["tiles"][0]
        _print_tile_info(tile["csv"], "csv")
        print()
        _print_tile_info(tile["mul"], "mul")
        print()
        print("Same:" if tile["same"] else "Different.")
        return 0

    print(
        f"Compared {result['compared']} tiles: "
        f"{result['different']} differ between CSV and MUL."
    )
    for tile in result["tiles"]:
        kind = tile["kind"]
        idx = tile["tile_index"]
        print(f"- {kind}[{idx}]", end="")
        if tile.get("graphic_id") is not None:
            print(f" ({tile['graphic_id']:#x})", end="")
        csv_info = tile["csv"]
        mul_info = tile["mul"]
        print(
            f" csv={csv_info['flags_hex']} mul={mul_info['flags_hex']}"
        )
    remaining = result["different"] - len(result["tiles"])
    if remaining > 0:
        print(f"... and {remaining} more (use --limit to show more)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ultima-tiledata",
        description=(
            "Manage tiledata via CSV in your repo, with optional preview against "
            "the client tiledata.mul."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser(
        "show",
        help="Show one tile from repo CSV, optionally compare with client MUL.",
    )
    show.add_argument("csv", help="Repo CSV file (source of truth)")
    show.add_argument(
        "--vs",
        metavar="MUL",
        help="Client tiledata.mul to compare against",
    )
    _add_tile_target(show)
    show.set_defaults(func=_cmd_show)

    set_flag = sub.add_parser(
        "set-flag",
        help="Patch flags in repo CSV (primary edit command).",
    )
    set_flag.add_argument("csv", help="Repo CSV file to edit")
    set_flag.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Output CSV (default: overwrite input)",
    )
    _add_tile_target(set_flag)
    set_flag.add_argument(
        "--add",
        action="append",
        default=[],
        metavar="FLAG",
        help="Flag to add, e.g. Stackable",
    )
    set_flag.add_argument(
        "--remove",
        action="append",
        default=[],
        metavar="FLAG",
    )
    set_flag.add_argument(
        "--set",
        metavar="FLAGS",
        help="Replace flags (names or hex), then apply --add/--remove",
    )
    set_flag.set_defaults(func=_cmd_set_flag)

    pull = sub.add_parser(
        "pull",
        help="Refresh repo CSV from client tiledata.mul (inspect before commit).",
    )
    pull.add_argument("mul", help="Client tiledata.mul")
    pull.add_argument("csv", help="Repo CSV output path")
    pull.set_defaults(func=_cmd_pull)

    build = sub.add_parser(
        "build",
        help="Build tiledata.mul from repo CSV for the game client.",
    )
    build.add_argument("csv", help="Repo CSV file")
    build.add_argument("mul", help="Output tiledata.mul")
    fmt = build.add_mutually_exclusive_group()
    fmt.add_argument(
        "--new-format",
        action="store_true",
        help="Force CV_7090+ layout (64-bit flags).",
    )
    fmt.add_argument(
        "--old-format",
        action="store_true",
        help="Force classic pre-CV_7090 layout (32-bit flags).",
    )
    build.add_argument(
        "--static-groups",
        type=int,
        default=None,
        metavar="N",
        help="Override static group count (default: from CSV metadata or max item id).",
    )
    build.set_defaults(func=_cmd_build)

    diff = sub.add_parser(
        "diff",
        help="Compare repo CSV against client tiledata.mul.",
    )
    diff.add_argument("csv", help="Repo CSV file")
    diff.add_argument("mul", help="Client tiledata.mul")
    _add_tile_target(diff, required=False)
    diff.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max differing tiles to print in full diff (default: 20)",
    )
    diff.set_defaults(func=_cmd_diff)

    # Legacy aliases
    to_csv = sub.add_parser("to-csv", help=argparse.SUPPRESS)
    to_csv.add_argument("input")
    to_csv.add_argument("output")
    to_csv.set_defaults(func=lambda a: _cmd_pull(argparse.Namespace(mul=a.input, csv=a.output)))

    from_csv = sub.add_parser("from-csv", help=argparse.SUPPRESS)
    from_csv.add_argument("input")
    from_csv.add_argument("output")
    from_csv.add_argument("--new-format", action="store_true")
    from_csv.add_argument("--static-groups", type=int, default=None)
    from_csv.set_defaults(
        func=lambda a: _cmd_build(
            argparse.Namespace(
                csv=a.input,
                mul=a.output,
                new_format=a.new_format,
                old_format=False,
                static_groups=a.static_groups,
            )
        )
    )

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except UltimaSdkException as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
