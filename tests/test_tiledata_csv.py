"""Tests for tiledata CSV conversion."""

import csv
import subprocess
import sys

import pytest

from ultima_sdk.exceptions import FileParseError
from ultima_sdk.tiledata import (
    CSV_FIELDNAMES,
    TileData,
    TileDataSnapshot,
    TileFlag,
    _LAND_SECTION_NEW,
    _LAND_TILE_COUNT,
    _STATIC_GROUP_NEW,
    _default_item_tile,
    _default_land_tile,
    _read_tiledata_csv_rows,
    flags_to_hex,
    flags_to_names,
    infer_new_format,
    parse_flags_from_csv_row,
)
from ultima_sdk.tiledata_cli import main as tiledata_cli_main


def _make_snapshot(
    *,
    new_format: bool = False,
    static_group_count: int = 512,
    land_overrides: dict[int, dict] | None = None,
    item_overrides: dict[int, dict] | None = None,
) -> TileDataSnapshot:
    item_count = static_group_count * 32
    land_tiles = [_default_land_tile() for _ in range(_LAND_TILE_COUNT)]
    item_tiles = [_default_item_tile() for _ in range(item_count)]

    for tile_id, fields in (land_overrides or {}).items():
        land_tiles[tile_id] = {**land_tiles[tile_id], **fields}
    for tile_id, fields in (item_overrides or {}).items():
        item_tiles[tile_id] = {**item_tiles[tile_id], **fields}

    return TileDataSnapshot(
        new_format=new_format,
        static_group_count=static_group_count,
        land_tiles=land_tiles,
        item_tiles=item_tiles,
    )


class TestTileDataCsv:
    def test_classic_old_format_round_trip(self, tmp_path: pytest.FixtureRequest) -> None:
        snapshot = _make_snapshot(
            land_overrides={0: {"flags": 1, "texture_id": 7, "name": "grass"}},
            item_overrides={
                100: {
                    "flags": 0x40,
                    "weight": 10,
                    "layer": 0,
                    "count": 1,
                    "height": 20,
                    "name": "chest",
                }
            },
        )
        mul_path = tmp_path / "tiledata.mul"
        TileData.save_snapshot(str(mul_path), snapshot)

        loaded = TileData.load_snapshot(str(mul_path))
        assert loaded.new_format is False
        assert loaded.static_group_count == 512
        assert loaded.land_tiles[0]["name"] == "grass"
        assert loaded.item_tiles[100]["name"] == "chest"
        assert loaded.item_tiles[100]["weight"] == 10

    def test_csv_round_trip(self, tmp_path: pytest.FixtureRequest) -> None:
        snapshot = _make_snapshot(
            land_overrides={1: {"name": "dirt", "texture_id": 3}},
            item_overrides={2: {"name": "rock", "height": 15, "flags": 0x80}},
        )
        src = tmp_path / "src.mul"
        csv_path = tmp_path / "tiles.csv"
        dst = tmp_path / "dst.mul"

        TileData.save_snapshot(str(src), snapshot)
        row_count = TileData.convert_to_csv(str(src), str(csv_path))
        assert row_count == _LAND_TILE_COUNT + 512 * 32

        with open(csv_path, encoding="utf-8", newline="") as fh:
            first_line = fh.readline()
        assert first_line.startswith("# ultima-tiledata:")
        assert "new_format=0" in first_line
        assert "static_groups=512" in first_line

        _metadata, rows = _read_tiledata_csv_rows(str(csv_path))
        assert rows[1]["kind"] == "land"
        assert rows[1]["name"] == "dirt"
        item_rows = [r for r in rows if r["kind"] == "item"]
        assert item_rows[2]["name"] == "rock"

        TileData.convert_from_csv(str(csv_path), str(dst))
        loaded = TileData.load_snapshot(str(dst))
        assert loaded.land_tiles[1]["name"] == "dirt"
        assert loaded.item_tiles[2]["name"] == "rock"
        assert loaded.item_tiles[2]["height"] == 15

    def test_modern_format_with_2048_static_groups(
        self, tmp_path: pytest.FixtureRequest
    ) -> None:
        snapshot = _make_snapshot(
            new_format=True,
            static_group_count=2048,
            land_overrides={
                0: {"flags": 0x1000000000, "texture_id": 2, "name": "modern land"}
            },
            item_overrides={
                65535: {
                    "flags": 0x2000000000,
                    "weight": 1,
                    "layer": 0,
                    "count": 0,
                    "anim_id": 0,
                    "hue": 0,
                    "light_index": 0,
                    "height": 0,
                    "name": "modern item",
                }
            },
        )
        mul_path = tmp_path / "modern.mul"
        TileData.save_snapshot(str(mul_path), snapshot)

        expected_size = _LAND_SECTION_NEW + 2048 * _STATIC_GROUP_NEW
        assert mul_path.stat().st_size == expected_size
        assert expected_size == 3_188_736

        loaded = TileData.load_snapshot(str(mul_path))
        assert loaded.new_format is True
        assert loaded.static_group_count == 2048
        assert len(loaded.item_tiles) == 65536
        assert loaded.land_tiles[0]["name"] == "modern land"
        assert loaded.item_tiles[65535]["name"] == "modern item"

    def test_legacy_item_aliases_on_get(self) -> None:
        snapshot = _make_snapshot(
            item_overrides={
                1: {
                    "layer": 5,
                    "count": 42,
                    "anim_id": 99,
                    "weight": 0,
                    "hue": 0,
                    "light_index": 0,
                    "height": 0,
                    "name": "alias test",
                }
            },
        )

        TileData._initialized = True
        TileData._new_format = snapshot.new_format
        TileData._static_group_count = snapshot.static_group_count
        TileData._land_tiles = snapshot.land_tiles
        TileData._item_tiles = snapshot.item_tiles

        item = TileData.get_item_tile(1)
        assert item is not None
        assert item["quality"] == 5
        assert item["quantity"] == 42
        assert item["anim"] == 99

        TileData._initialized = False

    def test_import_csv_unknown_kind_raises(self, tmp_path: pytest.FixtureRequest) -> None:
        csv_path = tmp_path / "bad.csv"
        with open(csv_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerow(
                {
                    "kind": "bogus",
                    "id": 0,
                    "flags_hex": "0x0",
                    "flags_names": "",
                    "texture_id": 0,
                    "weight": 0,
                    "layer": 0,
                    "count": 0,
                    "anim_id": 0,
                    "hue": 0,
                    "light_index": 0,
                    "height": 0,
                    "name": "x",
                }
            )
        with pytest.raises(FileParseError, match="unknown kind"):
            TileData.import_csv(str(csv_path))


class TestTileFlagCsv:
    def test_flags_to_hex_and_names(self) -> None:
        value = TileFlag.IMPASSABLE | TileFlag.SURFACE | TileFlag.BRIDGE
        assert flags_to_hex(value) == "0x640"
        assert "Impassable" in flags_to_names(value)
        assert "Surface" in flags_to_names(value)

    def test_parse_flag_names(self) -> None:
        row = {"flags_hex": "", "flags_names": "Impassable | Surface", "flags": ""}
        assert parse_flags_from_csv_row(row) == 0x240

    def test_parse_flags_hex_priority(self) -> None:
        row = {
            "flags_hex": "0x640",
            "flags_names": "Impassable",
            "flags": "999",
        }
        assert parse_flags_from_csv_row(row) == 0x640

    def test_flag_names_round_trip_in_csv(self, tmp_path: pytest.FixtureRequest) -> None:
        snapshot = _make_snapshot(
            item_overrides={
                10: {
                    "flags": 0x240,
                    "weight": 0,
                    "layer": 0,
                    "count": 0,
                    "anim_id": 0,
                    "hue": 0,
                    "light_index": 0,
                    "height": 0,
                    "name": "flagged",
                }
            },
        )
        src = tmp_path / "src.mul"
        csv_path = tmp_path / "flags.csv"
        dst = tmp_path / "dst.mul"
        TileData.save_snapshot(str(src), snapshot)
        TileData.convert_to_csv(str(src), str(csv_path))

        text = csv_path.read_text(encoding="utf-8")
        assert "0x240" in text
        assert "Impassable" in text

        # Edit by names: clear hex, keep names
        _metadata, rows = _read_tiledata_csv_rows(str(csv_path))
        for row in rows:
            if row["kind"] == "item" and row["id"] == "10":
                row["flags_hex"] = ""
                row["flags_names"] = "Impassable | Bridge"
        with open(csv_path, "w", encoding="utf-8", newline="") as fh:
            if _metadata:
                nf = 1 if _metadata.get("new_format") else 0
                groups = _metadata.get("static_groups", 512)
                fh.write(
                    f"# ultima-tiledata: new_format={nf} static_groups={groups}\n"
                )
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)

        TileData.convert_from_csv(str(csv_path), str(dst))
        loaded = TileData.load_snapshot(str(dst))
        assert loaded.item_tiles[10]["flags"] == (0x40 | 0x400)


class TestTileDataFlagPatch:
    def test_patch_add_flag_by_graphic_id(self, tmp_path: pytest.FixtureRequest) -> None:
        snapshot = _make_snapshot(
            item_overrides={
                0x123: {
                    "flags": TileFlag.SURFACE,
                    "weight": 0,
                    "layer": 0,
                    "count": 0,
                    "anim_id": 0,
                    "hue": 0,
                    "light_index": 0,
                    "height": 0,
                    "name": "test item",
                }
            },
        )
        src = tmp_path / "tiledata.mul"
        csv_path = tmp_path / "tiledata.csv"
        TileData.save_snapshot(str(src), snapshot)
        TileData.convert_to_csv(str(src), str(csv_path))

        result = TileData.patch_flags_csv(
            str(csv_path),
            item=0x4123,
            add=["Stackable"],
        )
        assert result["new_flags"] == (TileFlag.SURFACE | TileFlag.STACKABLE)

        rebuilt = tmp_path / "out.mul"
        TileData.convert_from_csv(str(csv_path), str(rebuilt))
        loaded = TileData.load_snapshot(str(rebuilt))
        assert loaded.item_tiles[0x123]["flags"] == result["new_flags"]

    def test_get_tile_info_from_csv(self, tmp_path: pytest.FixtureRequest) -> None:
        snapshot = _make_snapshot(
            item_overrides={
                5: {
                    "flags": TileFlag.CONTAINER,
                    "weight": 1,
                    "layer": 0,
                    "count": 0,
                    "anim_id": 0,
                    "hue": 0,
                    "light_index": 0,
                    "height": 0,
                    "name": "bag",
                }
            },
        )
        csv_path = tmp_path / "tiledata.csv"
        TileData.save_snapshot(str(tmp_path / "a.mul"), snapshot)
        TileData.convert_to_csv(str(tmp_path / "a.mul"), str(csv_path))

        info = TileData.get_tile_info(str(csv_path), item=0x4005)
        assert info["name"] == "bag"
        assert info["flags"] == TileFlag.CONTAINER

    def test_get_tile_info_csv_id_matches_row_not_graphic(
        self, tmp_path: pytest.FixtureRequest
    ) -> None:
        """Values >= 0x4000 in CSV id column are tiledata indices, not graphic ids."""
        tile_index = 45285
        snapshot = _make_snapshot(
            static_group_count=1416,
            item_overrides={
                tile_index: {
                    "flags": TileFlag.PARTIAL_HUE,
                    "weight": 0,
                    "layer": 0,
                    "count": 0,
                    "anim_id": 0,
                    "hue": 0,
                    "light_index": 0,
                    "height": 0,
                    "name": "bottle",
                }
            },
        )
        csv_path = tmp_path / "tiledata.csv"
        TileData.save_snapshot(str(tmp_path / "a.mul"), snapshot)
        TileData.convert_to_csv(str(tmp_path / "a.mul"), str(csv_path))

        info = TileData.get_tile_info(str(csv_path), item=tile_index)
        assert info["tile_index"] == tile_index
        assert info["name"] == "bottle"
        assert info["flags"] == TileFlag.PARTIAL_HUE
        assert info["graphic_id"] == tile_index + 0x4000

    def test_diff_csv_vs_mul_single_tile(self, tmp_path: pytest.FixtureRequest) -> None:
        base = _make_snapshot(
            item_overrides={
                1: {
                    "flags": TileFlag.STACKABLE,
                    "weight": 0,
                    "layer": 0,
                    "count": 0,
                    "anim_id": 0,
                    "hue": 0,
                    "light_index": 0,
                    "height": 0,
                    "name": "same",
                }
            },
        )
        csv_path = tmp_path / "tiledata.csv"
        mul_path = tmp_path / "client.mul"
        TileData.save_snapshot(str(mul_path), base)
        TileData.convert_to_csv(str(mul_path), str(csv_path))

        TileData.patch_flags_csv(str(csv_path), item=1, add=["Surface"])
        result = TileData.diff_csv_vs_mul(str(csv_path), str(mul_path), item=1)
        assert result["different"] == 1
        assert not result["tiles"][0]["same"]

    def test_patch_remove_flag_in_place(self, tmp_path: pytest.FixtureRequest) -> None:
        snapshot = _make_snapshot(
            land_overrides={
                1: {
                    "flags": TileFlag.IMPASSABLE | TileFlag.SURFACE,
                    "texture_id": 0,
                    "name": "blocked",
                }
            },
        )
        path = tmp_path / "tiledata.mul"
        TileData.save_snapshot(str(path), snapshot)

        TileData.patch_flags_file(
            str(path),
            item=None,
            land=1,
            remove=["Impassable"],
        )
        loaded = TileData.load_snapshot(str(path))
        assert loaded.land_tiles[1]["flags"] == TileFlag.SURFACE

    def test_cli_set_flag(self, tmp_path: pytest.FixtureRequest) -> None:
        snapshot = _make_snapshot(
            item_overrides={
                0x10: {
                    "flags": 0,
                    "weight": 0,
                    "layer": 0,
                    "count": 0,
                    "anim_id": 0,
                    "hue": 0,
                    "light_index": 0,
                    "height": 0,
                    "name": "coin",
                }
            },
        )
        mul = tmp_path / "tiledata.mul"
        csv_path = tmp_path / "tiledata.csv"
        TileData.save_snapshot(str(mul), snapshot)
        TileData.convert_to_csv(str(mul), str(csv_path))

        rc = tiledata_cli_main(
            ["set-flag", str(csv_path), "--item", "0x4010", "--add", "Stackable"]
        )
        assert rc == 0

        info = TileData.get_tile_info(str(csv_path), item=0x4010)
        assert info["flags"] == TileFlag.STACKABLE

    def test_cli_show_with_vs(self, tmp_path: pytest.FixtureRequest) -> None:
        snapshot = _make_snapshot(
            item_overrides={
                2: {
                    "flags": TileFlag.WALL,
                    "weight": 0,
                    "layer": 0,
                    "count": 0,
                    "anim_id": 0,
                    "hue": 0,
                    "light_index": 0,
                    "height": 0,
                    "name": "wall",
                }
            },
        )
        mul = tmp_path / "client.mul"
        csv_path = tmp_path / "tiledata.csv"
        TileData.save_snapshot(str(mul), snapshot)
        TileData.convert_to_csv(str(mul), str(csv_path))
        TileData.patch_flags_csv(str(csv_path), item=2, add=["Impassable"])

        rc = tiledata_cli_main(
            ["show", str(csv_path), "--vs", str(mul), "--item", "0x4002"]
        )
        assert rc == 0


class TestTileDataCli:
    def test_cli_round_trip(self, tmp_path: pytest.FixtureRequest) -> None:
        snapshot = _make_snapshot(
            land_overrides={5: {"name": "sand"}},
            item_overrides={9: {"name": "gold coin", "weight": 1}},
        )
        src = tmp_path / "tiledata.mul"
        csv_path = tmp_path / "tiledata.csv"
        dst = tmp_path / "out.mul"
        TileData.save_snapshot(str(src), snapshot)

        assert tiledata_cli_main(["to-csv", str(src), str(csv_path)]) == 0
        assert tiledata_cli_main(["from-csv", str(csv_path), str(dst)]) == 0

        loaded = TileData.load_snapshot(str(dst))
        assert loaded.land_tiles[5]["name"] == "sand"
        assert loaded.item_tiles[9]["name"] == "gold coin"

    def test_cli_module_invocation(self, tmp_path: pytest.FixtureRequest) -> None:
        snapshot = _make_snapshot(land_overrides={0: {"name": "water"}})
        src = tmp_path / "tiledata.mul"
        csv_path = tmp_path / "tiledata.csv"
        TileData.save_snapshot(str(src), snapshot)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ultima_sdk.tiledata_cli",
                "to-csv",
                str(src),
                str(csv_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert csv_path.exists()
        assert "water" in csv_path.read_text(encoding="utf-8")

    def test_build_auto_detects_new_format_from_group_count(
        self, tmp_path: pytest.FixtureRequest
    ) -> None:
        snapshot = _make_snapshot(
            new_format=True,
            static_group_count=2048,
            item_overrides={
                45285: {
                    "flags": TileFlag.PARTIAL_HUE,
                    "weight": 0,
                    "layer": 0,
                    "count": 0,
                    "anim_id": 0,
                    "hue": 0,
                    "light_index": 0,
                    "height": 0,
                    "name": "bottle",
                }
            },
        )
        csv_path = tmp_path / "tiledata.csv"
        dst = tmp_path / "built.mul"
        TileData.save_snapshot(str(tmp_path / "src.mul"), snapshot)
        TileData.convert_to_csv(str(tmp_path / "src.mul"), str(csv_path))

        TileData.convert_from_csv(str(csv_path), str(dst))
        loaded = TileData.load_snapshot(str(dst))
        assert loaded.new_format is True
        assert loaded.static_group_count == 2048
        assert loaded.item_tiles[45285]["name"] == "bottle"

    def test_build_auto_detects_from_existing_mul(
        self, tmp_path: pytest.FixtureRequest
    ) -> None:
        modern = _make_snapshot(new_format=True, static_group_count=512)
        classic = _make_snapshot(new_format=False, static_group_count=512)
        reference = tmp_path / "client.mul"
        csv_path = tmp_path / "tiledata.csv"
        dst = tmp_path / "out.mul"

        TileData.save_snapshot(str(reference), modern)
        TileData.save_snapshot(str(tmp_path / "classic.mul"), classic)
        TileData.convert_to_csv(str(tmp_path / "classic.mul"), str(csv_path))

        # CSV has classic metadata but target path already holds a modern client file.
        TileData.convert_from_csv(str(csv_path), str(reference))
        loaded = TileData.load_snapshot(str(reference))
        assert loaded.new_format is True

    def test_infer_new_format_prefers_metadata(self) -> None:
        assert (
            infer_new_format(
                metadata={"new_format": True},
                static_group_count=512,
            )
            is True
        )
        assert (
            infer_new_format(
                explicit=False,
                metadata={"new_format": True},
                static_group_count=2048,
            )
            is False
        )
        assert (
            infer_new_format(
                metadata={"new_format": False},
                static_group_count=2048,
            )
            is False
        )
