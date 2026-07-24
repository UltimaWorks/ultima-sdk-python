"""
TileData module - Manages tile properties and static item data.

Format follows ClassicUO's ``TileDataLoader`` (client >= CV_7090 uses 64-bit
flags and a variable number of static-tile groups):

Land section (always 512 groups x 32 tiles):
  Old (pre-CV_7090): uint32 flags, uint16 texture_id, char[20] name  -> 26 B
  New (CV_7090+):    uint64 flags, uint16 texture_id, char[20] name  -> 30 B

Static section (variable group count derived from file size):
  Old: uint32 flags, weight, layer, int32 count, anim_id, hue, light_index,
       height, char[20] name  -> 37 B per tile, 1188 B per group
  New: uint64 flags + same tail fields                     -> 41 B, 1316 B/group

Names are UTF-8 in current clients.
"""

from __future__ import annotations

import csv
import io
import os
import re
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional

from .binary_extensions import BinaryReader
from .exceptions import FileAccessException, FileParseError
from .files import Files


class TileFlag:
    """Tile flags (low 32 bits; new format also uses high bits)."""

    NONE = 0x00000000
    BACKGROUND = 0x00000001
    WEAPON = 0x00000002
    TRANSPARENT = 0x00000004
    TRANSLUCENT = 0x00000008
    WALL = 0x00000010
    DAMAGING = 0x00000020
    IMPASSABLE = 0x00000040
    WET = 0x00000080
    UNKNOWN1 = 0x00000100
    SURFACE = 0x00000200
    BRIDGE = 0x00000400
    STACKABLE = 0x00000800
    WINDOW = 0x00001000
    NO_SHOOT = 0x00002000
    ARTICLE_A = 0x00004000
    ARTICLE_AN = 0x00008000
    INTERNAL = 0x00010000
    FOLIAGE = 0x00020000
    PARTIAL_HUE = 0x00040000
    NO_HOUSE = 0x00080000
    MAP = 0x00100000
    CONTAINER = 0x00200000
    WEARABLE = 0x00400000
    LIGHT_SOURCE = 0x00800000
    ANIMATED = 0x01000000
    NO_DIAGONAL = 0x02000000
    UNKNOWN2 = 0x04000000
    ARMOR = 0x08000000
    ROOF = 0x10000000
    DOOR = 0x20000000
    STAIR_BACK = 0x40000000
    STAIR_RIGHT = 0x80000000
    # CV_7090+ extended flags
    ALPHA_BLEND = 0x0100000000
    USE_NEW_ART = 0x0200000000
    ART_USED = 0x0400000000
    NO_SHADOW = 0x1000000000
    PIXEL_BLEED = 0x2000000000
    PLAY_ANIM_ONCE = 0x4000000000
    MULTI_MOVABLE = 0x10000000000


def _build_flag_lookup() -> Dict[str, int]:
    """Build a case-insensitive name -> value map from :class:`TileFlag`."""
    lookup: Dict[str, int] = {}
    for name, value in vars(TileFlag).items():
        if name.isupper() and isinstance(value, int):
            lookup[name] = value
    # Common aliases used by ClassicUO and community tools.
    aliases = {
        "ANIMATION": TileFlag.ANIMATED,
        "GENERIC": TileFlag.STACKABLE,
        "STACKABLE": TileFlag.STACKABLE,
        "LIGHT": TileFlag.LIGHT_SOURCE,
        "STAIRS": TileFlag.STAIR_BACK,
        "LAVA": TileFlag.DAMAGING,
        "HOVEROVER": TileFlag.NO_DIAGONAL,
        "ARTICULATED": TileFlag.ARTICLE_A,
        "UNKNOWNX": TileFlag.STAIR_RIGHT,
    }
    lookup.update(aliases)
    return lookup


_FLAG_LOOKUP: Dict[str, int] = _build_flag_lookup()
_FLAG_NAMES_BY_VALUE: Dict[int, str] = {
    value: name
    for name, value in vars(TileFlag).items()
    if name.isupper() and isinstance(value, int)
}


def flags_to_hex(value: int) -> str:
    """Format tile flags as a hex literal for CSV export."""
    if value == 0:
        return "0x0"
    if value > 0xFFFFFFFF:
        return f"0x{value:016X}"
    return f"0x{value:X}"


def flags_to_names(value: int) -> str:
    """Format tile flags as readable names, e.g. ``Impassable | Surface``."""
    if value == 0:
        return ""

    remaining = value
    parts: List[str] = []
    for bit, name in sorted(_FLAG_NAMES_BY_VALUE.items(), reverse=True):
        if remaining & bit:
            parts.append(name.title().replace("_", " "))
            remaining &= ~bit

    if remaining:
        parts.append(flags_to_hex(remaining))

    parts.reverse()
    return " | ".join(parts)


def parse_flags_from_csv_row(
    row: Dict[str, str | None],
    *,
    csv_path: str = "",
    line_no: int = 0,
) -> int:
    """Parse flags from CSV columns.

    Priority:
    1. ``flags_hex`` – e.g. ``0x640`` (edit this for precise hex values)
    2. ``flags_names`` – e.g. ``Impassable | Surface`` (clear ``flags_hex`` first)
    3. ``flags`` – legacy decimal column from older CSV exports
    """
    hex_value = (row.get("flags_hex") or "").strip()
    if hex_value:
        try:
            return int(hex_value, 0)
        except ValueError as exc:
            location = f"{csv_path} line {line_no}: " if line_no else ""
            raise FileParseError(
                f"{location}invalid flags_hex value {hex_value!r}"
            ) from exc

    names = (row.get("flags_names") or "").strip()
    if names:
        return _parse_flag_names(names, csv_path=csv_path, line_no=line_no)

    return _csv_int(row.get("flags"))


def _parse_flag_names(text: str, *, csv_path: str = "", line_no: int = 0) -> int:
    location = f"{csv_path} line {line_no}: " if line_no else ""
    result = 0
    for token in re.split(r"[|,;]+", text):
        token = token.strip()
        if not token:
            continue
        if token.lower().startswith("0x"):
            try:
                result |= int(token, 16)
            except ValueError as exc:
                raise FileParseError(
                    f"{location}invalid hex flag token {token!r}"
                ) from exc
            continue

        key = token.upper().replace(" ", "_").replace("-", "_")
        if key not in _FLAG_LOOKUP:
            known = ", ".join(sorted(_FLAG_LOOKUP))
            raise FileParseError(
                f"{location}unknown flag name {token!r}. "
                f"Use names like Impassable, Surface, Bridge, or hex in flags_hex."
            )
        result |= _FLAG_LOOKUP[key]
    return result


def parse_flag_names_list(names: List[str]) -> int:
    """Combine multiple flag names/hex tokens into a single bitmask."""
    if not names:
        return 0
    return _parse_flag_names(",".join(names))


def resolve_item_tile_index(item_ref: int) -> int:
    """Convert an UO graphic id to a tiledata item index.

    Values ``>= 0x4000`` are treated as graphic ids (``0x4123`` -> index ``0x123``).
    Smaller values are treated as direct tiledata indices.
    """
    if item_ref < 0:
        raise FileParseError(f"Invalid item id: {item_ref}")
    if item_ref >= 0x4000:
        return item_ref - 0x4000
    return item_ref


def resolve_item_index(
    item_ref: int,
    *,
    csv_path: str | None = None,
    use_index: bool = False,
) -> int:
    """Resolve an item reference to a tiledata index.

    Resolution order:
    1. ``use_index=True`` (``--index`` flag) – use *item_ref* as tiledata index
    2. When *csv_path* is given, match CSV ``id`` column exactly
    3. Fall back to :func:`resolve_item_tile_index` (UO graphic id convention)
    """
    if use_index:
        return item_ref

    if csv_path:
        for row in TileData._load_csv_rows(csv_path):
            if (row.get("kind") or "").strip().lower() != "item":
                continue
            if _csv_int(row.get("id")) == item_ref:
                return item_ref

    return resolve_item_tile_index(item_ref)


def item_graphic_id(tile_index: int) -> int:
    """Return the in-game graphic id for a tiledata item index."""
    return tile_index + 0x4000


def apply_flag_patch(
    current: int,
    *,
    add: int = 0,
    remove: int = 0,
    set_to: int | None = None,
) -> int:
    """Apply add/remove/set operations to a flag bitmask."""
    value = current if set_to is None else set_to
    value |= add
    value &= ~remove
    return value


def resolve_tile_target(
    *,
    item: int | None = None,
    land: int | None = None,
    csv_path: str | None = None,
    use_index: bool = False,
) -> tuple[str, int]:
    """Return ``(kind, tile_index)`` for an item or land selector."""
    if (item is None) == (land is None):
        raise FileParseError(
            "Specify exactly one of item=, index= (--index), or land="
        )
    if item is not None:
        return "item", resolve_item_index(
            item, csv_path=csv_path, use_index=use_index
        )
    assert land is not None
    return "land", land


def entry_to_tile_info(kind: str, tile_index: int, entry: Dict) -> Dict[str, object]:
    """Build a display-friendly tile summary from a parsed entry."""
    flags = int(entry["flags"])
    info: Dict[str, object] = {
        "kind": kind,
        "tile_index": tile_index,
        "graphic_id": item_graphic_id(tile_index) if kind == "item" else None,
        "name": entry.get("name", ""),
        "flags": flags,
        "flags_hex": flags_to_hex(flags),
        "flags_names": flags_to_names(flags),
    }
    if kind == "land":
        info["texture_id"] = entry.get("texture_id", 0)
    else:
        info.update(
            {
                "weight": entry.get("weight", 0),
                "layer": entry.get("layer", 0),
                "count": entry.get("count", 0),
                "anim_id": entry.get("anim_id", 0),
                "hue": entry.get("hue", 0),
                "light_index": entry.get("light_index", 0),
                "height": entry.get("height", 0),
            }
        )
    return info


def _entry_from_csv_row(row: Dict[str, str]) -> tuple[str, int, Dict]:
    kind = (row.get("kind") or "").strip().lower()
    tile_index = _csv_int(row.get("id"))
    flags = parse_flags_from_csv_row(row)
    name = (row.get("name") or "").strip()
    if kind == "land":
        entry: Dict = {
            "flags": flags,
            "texture_id": _csv_int(row.get("texture_id")),
            "name": name,
        }
    elif kind == "item":
        entry = {
            "flags": flags,
            "weight": _csv_int(row.get("weight")),
            "layer": _csv_int(row.get("layer")),
            "count": _csv_int(row.get("count")),
            "anim_id": _csv_int(row.get("anim_id")),
            "hue": _csv_int(row.get("hue")),
            "light_index": _csv_int(row.get("light_index")),
            "height": _csv_int(row.get("height")),
            "name": name,
        }
    else:
        raise FileParseError(f"Unknown tile kind in CSV row: {kind!r}")
    return kind, tile_index, entry


def _make_patch_summary(
    *,
    kind: str,
    tile_index: int,
    graphic_id: int | None,
    name: str,
    old_flags: int,
    new_flags: int,
    output_path: str,
    source: str,
) -> Dict[str, object]:
    return {
        "kind": kind,
        "tile_index": tile_index,
        "graphic_id": graphic_id,
        "name": name,
        "old_flags": old_flags,
        "new_flags": new_flags,
        "old_flags_hex": flags_to_hex(old_flags),
        "new_flags_hex": flags_to_hex(new_flags),
        "old_flags_names": flags_to_names(old_flags),
        "new_flags_names": flags_to_names(new_flags),
        "output_path": output_path,
        "source": source,
    }
_LAND_ENTRY_OLD = 26
_LAND_ENTRY_NEW = 30
_ITEM_ENTRY_OLD = 37
_ITEM_ENTRY_NEW = 41

_LAND_GROUP_ENTRIES = 32
_ITEM_GROUP_ENTRIES = 32
_LAND_GROUPS = 512

_LAND_SECTION_OLD = _LAND_GROUPS * (4 + _LAND_GROUP_ENTRIES * _LAND_ENTRY_OLD)
_LAND_SECTION_NEW = _LAND_GROUPS * (4 + _LAND_GROUP_ENTRIES * _LAND_ENTRY_NEW)
_STATIC_GROUP_OLD = 4 + _ITEM_GROUP_ENTRIES * _ITEM_ENTRY_OLD
_STATIC_GROUP_NEW = 4 + _ITEM_GROUP_ENTRIES * _ITEM_ENTRY_NEW

_LAND_TILE_COUNT = _LAND_GROUPS * _LAND_GROUP_ENTRIES

CSV_FIELDNAMES: tuple[str, ...] = (
    "kind",
    "id",
    "flags_hex",
    "flags_names",
    "texture_id",
    "weight",
    "layer",
    "count",
    "anim_id",
    "hue",
    "light_index",
    "height",
    "name",
)

_CSV_METADATA_PREFIX = "# ultima-tiledata:"
_FLAG32_MASK = 0xFFFFFFFF


def _parse_csv_metadata_line(line: str) -> Dict[str, int | bool]:
    """Parse ``# ultima-tiledata: new_format=1 static_groups=2048``."""
    result: Dict[str, int | bool] = {}
    stripped = line.strip()
    if not stripped.startswith(_CSV_METADATA_PREFIX):
        return result

    payload = stripped[len(_CSV_METADATA_PREFIX) :].strip()
    for part in payload.split():
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key == "new_format":
            result["new_format"] = value.lower() not in ("0", "false", "no")
        elif key == "static_groups":
            result["static_groups"] = int(value, 0)
    return result


def _format_csv_metadata(snapshot: "TileDataSnapshot") -> str:
    nf = 1 if snapshot.new_format else 0
    return (
        f"{_CSV_METADATA_PREFIX} new_format={nf} "
        f"static_groups={snapshot.static_group_count}\n"
    )


def _read_tiledata_csv_rows(
    csv_path: str,
) -> tuple[Dict[str, int | bool], List[Dict[str, str]]]:
    """Load tiledata CSV rows, skipping optional metadata/comment header lines."""
    metadata: Dict[str, int | bool] = {}
    body_lines: List[str] = []
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith(_CSV_METADATA_PREFIX):
                    metadata.update(_parse_csv_metadata_line(line))
                    continue
                if stripped.startswith("#") or not stripped:
                    continue
                body_lines.append(line)
    except OSError as exc:
        raise FileAccessException(
            f"Cannot read tiledata CSV {csv_path!r}: {exc}"
        ) from exc

    if not body_lines:
        raise FileParseError(f"Empty CSV file: {csv_path!r}")

    reader = csv.DictReader(io.StringIO("".join(body_lines)))
    if reader.fieldnames is None:
        raise FileParseError(f"Empty CSV file: {csv_path!r}")
    return metadata, list(reader)


def infer_new_format(
    *,
    explicit: bool | None = None,
    metadata: Dict[str, int | bool] | None = None,
    max_flags: int = 0,
    static_group_count: int = 512,
    reference_mul_path: str | None = None,
) -> bool:
    """Choose classic vs CV_7090+ layout when building tiledata.mul from CSV."""
    if explicit is not None:
        return explicit

    if reference_mul_path:
        try:
            file_size = os.path.getsize(reference_mul_path)
        except OSError:
            pass
        else:
            new_format, _ = TileData._analyze_file_size(file_size)
            return new_format

    meta = metadata or {}
    if "new_format" in meta:
        return bool(meta["new_format"])

    if max_flags > _FLAG32_MASK:
        return True

    if static_group_count > 512:
        return True

    return False


@dataclass
class TileDataSnapshot:
    """In-memory representation of a full ``tiledata.mul`` file."""

    new_format: bool
    static_group_count: int
    land_tiles: List[Dict]
    item_tiles: List[Dict]

    @property
    def uoahs(self) -> bool:
        """Deprecated alias for :attr:`new_format`."""
        return self.new_format


def _default_land_tile() -> Dict:
    return {"flags": 0, "texture_id": 0, "name": ""}


def _default_item_tile() -> Dict:
    return {
        "flags": 0,
        "weight": 0,
        "layer": 0,
        "count": 0,
        "anim_id": 0,
        "hue": 0,
        "light_index": 0,
        "height": 0,
        "name": "",
    }


def _csv_int(value: object | None, default: int = 0) -> int:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return int(text, 0)


def _normalize_item_patch_fields(fields: Dict) -> Dict:
    """Map legacy field names onto the ClassicUO layout."""
    out = dict(fields)
    aliases = {
        "quality": "layer",
        "quantity": "count",
        "anim": "anim_id",
    }
    for old_key, new_key in aliases.items():
        if old_key in out and new_key not in out:
            out[new_key] = out.pop(old_key)
    return out


def _with_legacy_item_aliases(entry: Dict) -> Dict:
    """Return a copy with legacy aliases for older SDK callers."""
    out = dict(entry)
    out.setdefault("quality", out.get("layer", 0))
    out.setdefault("quantity", out.get("count", 0) & 0xFF)
    out.setdefault("anim", out.get("anim_id", 0))
    return out


class TileData:
    """Static class for managing tile data."""

    _land_tiles: List[Dict] = []
    _item_tiles: List[Dict] = []
    _initialized = False
    _new_format: bool = False
    _static_group_count: int = 512

    # ------------------------------------------------------------------
    # Format helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _analyze_file_size(file_size: int) -> tuple[bool, int]:
        """Detect ClassicUO old/new format and static group count."""
        candidates: list[tuple[bool, int, int]] = [
            (False, _LAND_SECTION_OLD, _STATIC_GROUP_OLD),
            (True, _LAND_SECTION_NEW, _STATIC_GROUP_NEW),
        ]
        for new_format, land_section, group_size in candidates:
            if file_size <= land_section:
                continue
            remainder = file_size - land_section
            if remainder % group_size != 0:
                continue
            static_group_count = remainder // group_size
            if static_group_count <= 0:
                continue
            return new_format, static_group_count

        raise FileParseError(
            f"Unexpected tiledata.mul size: {file_size} bytes. "
            f"Expected land section ({_LAND_SECTION_OLD} classic or "
            f"{_LAND_SECTION_NEW} modern) plus a whole number of static groups "
            f"({_STATIC_GROUP_OLD} or {_STATIC_GROUP_NEW} bytes each)."
        )

    @staticmethod
    def _item_tile_count(static_group_count: int) -> int:
        return static_group_count * _ITEM_GROUP_ENTRIES

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    @classmethod
    def initialize(cls) -> bool:
        """Load tile data from file."""
        if cls._initialized:
            return True
        try:
            path = Files.get_file_path("tiledata.mul")
            if not path:
                return False
            snapshot = cls.load_snapshot(path)
            cls._new_format = snapshot.new_format
            cls._static_group_count = snapshot.static_group_count
            cls._land_tiles = snapshot.land_tiles
            cls._item_tiles = snapshot.item_tiles
            cls._initialized = True
            return True
        except Exception as e:
            raise FileAccessException(f"Failed to load tiledata.mul: {e}")

    @classmethod
    def _load_tiledata(
        cls,
        reader: BinaryReader,
        *,
        new_format: bool,
        static_group_count: int,
    ) -> tuple[List[Dict], List[Dict]]:
        land_tiles: List[Dict] = []
        for _group in range(_LAND_GROUPS):
            reader.read_uint32()
            for _ in range(_LAND_GROUP_ENTRIES):
                land_tiles.append(
                    TileData._read_land_tile_entry(reader, new_format)
                )

        item_tiles: List[Dict] = []
        for _group in range(static_group_count):
            reader.read_uint32()
            for _ in range(_ITEM_GROUP_ENTRIES):
                item_tiles.append(
                    TileData._read_item_tile_entry(reader, new_format)
                )

        return land_tiles, item_tiles

    @staticmethod
    def _read_land_tile_entry(reader: BinaryReader, new_format: bool) -> Dict:
        flags = reader.read_uint64() if new_format else reader.read_uint32()
        return {
            "flags": flags,
            "texture_id": reader.read_uint16(),
            "name": reader.read_string(20, encoding="utf-8").strip("\x00"),
        }

    @staticmethod
    def _read_item_tile_entry(reader: BinaryReader, new_format: bool) -> Dict:
        flags = reader.read_uint64() if new_format else reader.read_uint32()
        return {
            "flags": flags,
            "weight": reader.read_byte(),
            "layer": reader.read_byte(),
            "count": reader.read_int32(),
            "anim_id": reader.read_uint16(),
            "hue": reader.read_uint16(),
            "light_index": reader.read_uint16(),
            "height": reader.read_byte(),
            "name": reader.read_string(20, encoding="utf-8").strip("\x00"),
        }

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @classmethod
    def get_land_tile(cls, id: int) -> Optional[Dict]:
        if not cls._initialized:
            cls.initialize()
        if 0 <= id < len(cls._land_tiles):
            return cls._land_tiles[id]
        return None

    @classmethod
    def get_item_tile(cls, id: int) -> Optional[Dict]:
        if not cls._initialized:
            cls.initialize()
        if 0 <= id < len(cls._item_tiles):
            return _with_legacy_item_aliases(cls._item_tiles[id])
        return None

    # ------------------------------------------------------------------
    # In-memory patch
    # ------------------------------------------------------------------

    @classmethod
    def set_land_tile(cls, id: int, **fields) -> None:
        """Patch land tile fields in memory. Call :meth:`save` to persist."""
        if not cls._initialized:
            cls.initialize()
        if not (0 <= id < len(cls._land_tiles)):
            raise IndexError(f"Land tile id {id} out of range")
        entry = dict(cls._land_tiles[id])
        for k, v in fields.items():
            if k not in entry:
                raise KeyError(f"Unknown land tile field: {k!r}")
            entry[k] = v
        cls._land_tiles[id] = entry

    @classmethod
    def set_item_tile(cls, id: int, **fields) -> None:
        """Patch item tile fields in memory. Call :meth:`save` to persist."""
        if not cls._initialized:
            cls.initialize()
        if not (0 <= id < len(cls._item_tiles)):
            raise IndexError(f"Item tile id {id} out of range")
        entry = dict(cls._item_tiles[id])
        normalized = _normalize_item_patch_fields(fields)
        for k, v in normalized.items():
            if k not in entry:
                raise KeyError(f"Unknown item tile field: {k!r}")
            entry[k] = v
        cls._item_tiles[id] = entry

    # ------------------------------------------------------------------
    # Serialise helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pack_name(name: str, length: int = 20) -> bytes:
        encoded = name.encode("utf-8", errors="replace")[:length]
        return encoded.ljust(length, b"\x00")

    @staticmethod
    def _encode_land_entry(entry: Dict, new_format: bool) -> bytes:
        if new_format:
            out = struct.pack("<QH", entry["flags"], entry["texture_id"])
        else:
            out = struct.pack("<IH", entry["flags"] & 0xFFFFFFFF, entry["texture_id"])
        return out + TileData._pack_name(entry["name"])

    @staticmethod
    def _encode_item_entry(entry: Dict, new_format: bool) -> bytes:
        if new_format:
            prefix = struct.pack("<Q", entry["flags"])
        else:
            prefix = struct.pack("<I", entry["flags"] & 0xFFFFFFFF)
        return (
            prefix
            + struct.pack(
                "<BBiHHHB",
                entry["weight"],
                entry["layer"],
                entry["count"],
                entry["anim_id"],
                entry["hue"],
                entry["light_index"],
                entry["height"],
            )
            + TileData._pack_name(entry["name"])
        )

    @staticmethod
    def _write_snapshot(fh, snapshot: TileDataSnapshot) -> None:
        for g in range(_LAND_GROUPS):
            fh.write(struct.pack("<I", 0))
            for i in range(_LAND_GROUP_ENTRIES):
                idx = g * _LAND_GROUP_ENTRIES + i
                fh.write(
                    TileData._encode_land_entry(
                        snapshot.land_tiles[idx], snapshot.new_format
                    )
                )

        for g in range(snapshot.static_group_count):
            fh.write(struct.pack("<I", 0))
            for i in range(_ITEM_GROUP_ENTRIES):
                idx = g * _ITEM_GROUP_ENTRIES + i
                fh.write(
                    TileData._encode_item_entry(
                        snapshot.item_tiles[idx], snapshot.new_format
                    )
                )

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    @classmethod
    def save(cls, path: str | None = None) -> None:
        """Write the full tiledata.mul to *path*."""
        if not cls._initialized:
            cls.initialize()

        if path is None:
            path = Files.get_file_path("tiledata.mul")
        if not path:
            raise FileAccessException("No tiledata.mul path available")

        snapshot = TileDataSnapshot(
            new_format=cls._new_format,
            static_group_count=cls._static_group_count,
            land_tiles=cls._land_tiles,
            item_tiles=cls._item_tiles,
        )
        with open(path, "wb") as f:
            cls._write_snapshot(f, snapshot)

    # ------------------------------------------------------------------
    # Verdata patch integration
    # ------------------------------------------------------------------

    @classmethod
    def apply_verdata_patch(cls, block_id: int, data: bytes, extra: int = 0) -> None:
        """Apply a raw verdata.mul patch to the in-memory tile cache."""
        if not cls._initialized:
            cls.initialize()

        reader = BinaryReader(io.BytesIO(data))

        if block_id < 0x4000:
            tile_id = block_id
            if not (0 <= tile_id < len(cls._land_tiles)):
                return
            cls._land_tiles[tile_id] = cls._read_land_tile_entry(
                reader, cls._new_format
            )
        else:
            tile_id = block_id - 0x4000
            if not (0 <= tile_id < len(cls._item_tiles)):
                return
            cls._item_tiles[tile_id] = cls._read_item_tile_entry(
                reader, cls._new_format
            )

    # ------------------------------------------------------------------
    # CSV and binary conversion
    # ------------------------------------------------------------------

    @staticmethod
    def load_snapshot(path: str) -> TileDataSnapshot:
        """Load a tiledata.mul file without touching the class-level cache."""
        try:
            file_size = os.path.getsize(path)
        except OSError as exc:
            raise FileAccessException(
                f"Cannot read tiledata.mul {path!r}: {exc}"
            ) from exc

        new_format, static_group_count = TileData._analyze_file_size(file_size)
        expected_items = TileData._item_tile_count(static_group_count)

        try:
            with open(path, "rb") as fh:
                reader = BinaryReader(fh)
                land_tiles, item_tiles = TileData._load_tiledata(
                    reader,
                    new_format=new_format,
                    static_group_count=static_group_count,
                )
        except OSError as exc:
            raise FileAccessException(
                f"Cannot read tiledata.mul {path!r}: {exc}"
            ) from exc
        except Exception as exc:
            raise FileParseError(
                f"Failed to parse tiledata.mul {path!r}: {exc}"
            ) from exc

        if len(land_tiles) != _LAND_TILE_COUNT:
            raise FileParseError(
                f"Expected {_LAND_TILE_COUNT} land tiles, got {len(land_tiles)}"
            )
        if len(item_tiles) != expected_items:
            raise FileParseError(
                f"Expected {expected_items} item tiles, got {len(item_tiles)}"
            )

        return TileDataSnapshot(
            new_format=new_format,
            static_group_count=static_group_count,
            land_tiles=land_tiles,
            item_tiles=item_tiles,
        )

    @staticmethod
    def build_bytes(snapshot: TileDataSnapshot) -> bytes:
        """Serialize a snapshot to raw tiledata.mul bytes."""
        expected_items = TileData._item_tile_count(snapshot.static_group_count)
        if len(snapshot.land_tiles) != _LAND_TILE_COUNT:
            raise FileParseError(
                f"Expected {_LAND_TILE_COUNT} land tiles, got {len(snapshot.land_tiles)}"
            )
        if len(snapshot.item_tiles) != expected_items:
            raise FileParseError(
                f"Expected {expected_items} item tiles, got {len(snapshot.item_tiles)}"
            )

        buf = io.BytesIO()
        TileData._write_snapshot(buf, snapshot)
        return buf.getvalue()

    @staticmethod
    def save_snapshot(path: str, snapshot: TileDataSnapshot) -> None:
        """Write a snapshot to a tiledata.mul file."""
        try:
            with open(path, "wb") as fh:
                fh.write(TileData.build_bytes(snapshot))
        except OSError as exc:
            raise FileAccessException(
                f"Cannot write tiledata.mul {path!r}: {exc}"
            ) from exc

    @staticmethod
    def _land_row(entry: Dict, tile_id: int) -> Dict[str, object]:
        flags = entry["flags"]
        return {
            "kind": "land",
            "id": tile_id,
            "flags_hex": flags_to_hex(flags),
            "flags_names": flags_to_names(flags),
            "texture_id": entry["texture_id"],
            "weight": "",
            "layer": "",
            "count": "",
            "anim_id": "",
            "hue": "",
            "light_index": "",
            "height": "",
            "name": entry["name"],
        }

    @staticmethod
    def _item_row(entry: Dict, tile_id: int) -> Dict[str, object]:
        flags = entry["flags"]
        return {
            "kind": "item",
            "id": tile_id,
            "flags_hex": flags_to_hex(flags),
            "flags_names": flags_to_names(flags),
            "texture_id": "",
            "weight": entry["weight"],
            "layer": entry["layer"],
            "count": entry["count"],
            "anim_id": entry["anim_id"],
            "hue": entry["hue"],
            "light_index": entry["light_index"],
            "height": entry["height"],
            "name": entry["name"],
        }

    @staticmethod
    def export_csv(
        csv_path: str,
        snapshot: TileDataSnapshot | None = None,
    ) -> int:
        """Write land and item tiles to a single CSV file."""
        if snapshot is None:
            if not TileData._initialized:
                TileData.initialize()
            snapshot = TileDataSnapshot(
                new_format=TileData._new_format,
                static_group_count=TileData._static_group_count,
                land_tiles=TileData._land_tiles,
                item_tiles=TileData._item_tiles,
            )

        rows: List[Dict[str, object]] = []
        for tile_id, entry in enumerate(snapshot.land_tiles):
            rows.append(TileData._land_row(entry, tile_id))
        for tile_id, entry in enumerate(snapshot.item_tiles):
            rows.append(TileData._item_row(entry, tile_id))

        try:
            with open(csv_path, "w", encoding="utf-8", newline="") as fh:
                fh.write(_format_csv_metadata(snapshot))
                writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
                writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            raise FileAccessException(
                f"Cannot write tiledata CSV {csv_path!r}: {exc}"
            ) from exc

        return len(rows)

    @staticmethod
    def _required_static_groups(max_item_id: int) -> int:
        return (max_item_id // _ITEM_GROUP_ENTRIES) + 1

    @staticmethod
    def import_csv(
        csv_path: str,
        *,
        new_format: bool | None = None,
        static_group_count: int | None = None,
        reference_mul_path: str | None = None,
    ) -> TileDataSnapshot:
        """Load a tiledata CSV exported by :meth:`export_csv`.

        When *new_format* is ``None`` (default), the layout is inferred from CSV
        metadata (written by :meth:`export_csv`), an existing *reference_mul_path*,
        64-bit flag values, or static group count (>512 implies modern client).
        """
        land_tiles = [_default_land_tile() for _ in range(_LAND_TILE_COUNT)]
        max_item_id = -1
        max_flags = 0
        pending_items: Dict[int, Dict] = {}

        metadata, rows = _read_tiledata_csv_rows(csv_path)
        fields = set()
        for row in rows:
            if row:
                fields = {name.strip().lower() for name in row.keys() if name}
                break
        required = {"kind", "id", "name"}
        if not required.issubset(fields):
            raise FileParseError(
                f"CSV must contain columns {sorted(required)}, got: {sorted(fields)!r}"
            )

        for line_no, row in enumerate(rows, start=2):
            kind = (row.get("kind") or "").strip().lower()
            if not kind:
                continue

            tile_id = _csv_int(row.get("id"), default=-1)
            if tile_id < 0:
                raise FileParseError(
                    f"{csv_path!r} line {line_no}: missing or invalid id"
                )

            flags = parse_flags_from_csv_row(
                row, csv_path=csv_path, line_no=line_no
            )
            max_flags = max(max_flags, flags)
            name = (row.get("name") or "").strip()

            if kind == "land":
                if tile_id >= _LAND_TILE_COUNT:
                    raise FileParseError(
                        f"{csv_path!r} line {line_no}: land id out of range"
                    )
                land_tiles[tile_id] = {
                    "flags": flags,
                    "texture_id": _csv_int(row.get("texture_id")),
                    "name": name,
                }
            elif kind == "item":
                layer = _csv_int(row.get("layer"))
                if not layer and row.get("quality"):
                    layer = _csv_int(row.get("quality"))
                count = _csv_int(row.get("count"))
                if not count and row.get("quantity"):
                    count = _csv_int(row.get("quantity"))
                anim_id = _csv_int(row.get("anim_id"))
                if not anim_id and row.get("anim"):
                    anim_id = _csv_int(row.get("anim"))

                pending_items[tile_id] = {
                    "flags": flags,
                    "weight": _csv_int(row.get("weight")),
                    "layer": layer,
                    "count": count,
                    "anim_id": anim_id,
                    "hue": _csv_int(row.get("hue")),
                    "light_index": _csv_int(row.get("light_index")),
                    "height": _csv_int(row.get("height")),
                    "name": name,
                }
                max_item_id = max(max_item_id, tile_id)
            else:
                raise FileParseError(
                    f"{csv_path!r} line {line_no}: unknown kind {kind!r} "
                    "(expected 'land' or 'item')"
                )

        if static_group_count is None:
            if "static_groups" in metadata:
                static_group_count = int(metadata["static_groups"])
            elif max_item_id >= 0:
                static_group_count = TileData._required_static_groups(max_item_id)
            else:
                static_group_count = 512

        resolved_new_format = infer_new_format(
            explicit=new_format,
            metadata=metadata,
            max_flags=max_flags,
            static_group_count=static_group_count,
            reference_mul_path=reference_mul_path,
        )

        item_count = TileData._item_tile_count(static_group_count)
        item_tiles = [_default_item_tile() for _ in range(item_count)]
        for tile_id, entry in pending_items.items():
            if tile_id >= item_count:
                raise FileParseError(
                    f"Item id {tile_id} requires at least "
                    f"{TileData._required_static_groups(tile_id)} static groups "
                    f"(currently {static_group_count})"
                )
            item_tiles[tile_id] = entry

        return TileDataSnapshot(
            new_format=resolved_new_format,
            static_group_count=static_group_count,
            land_tiles=land_tiles,
            item_tiles=item_tiles,
        )

    @staticmethod
    def convert_to_csv(mul_path: str, csv_path: str) -> int:
        """Convert tiledata.mul to CSV. Returns the number of rows written."""
        snapshot = TileData.load_snapshot(mul_path)
        return TileData.export_csv(csv_path, snapshot)

    @staticmethod
    def convert_from_csv(
        csv_path: str,
        mul_path: str,
        *,
        new_format: bool | None = None,
        static_group_count: int | None = None,
    ) -> int:
        """Convert CSV to tiledata.mul. Returns the number of rows imported."""
        snapshot = TileData.import_csv(
            csv_path,
            new_format=new_format,
            static_group_count=static_group_count,
            reference_mul_path=mul_path,
        )
        TileData.save_snapshot(mul_path, snapshot)
        return len(snapshot.land_tiles) + len(snapshot.item_tiles)

    @staticmethod
    def patch_flags_file(
        input_path: str,
        output_path: str | None = None,
        *,
        item: int | None = None,
        land: int | None = None,
        add: List[str] | None = None,
        remove: List[str] | None = None,
        set_flags: int | None = None,
        use_index: bool = False,
    ) -> Dict[str, object]:
        """Patch tile flags in a tiledata.mul file and save the result."""
        if (item is None) == (land is None):
            raise FileParseError("Specify exactly one of item= or land=")

        add_mask = parse_flag_names_list(add or [])
        remove_mask = parse_flag_names_list(remove or [])

        if not add_mask and not remove_mask and set_flags is None:
            raise FileParseError(
                "Nothing to do: provide --add, --remove, or --set"
            )

        snapshot = TileData.load_snapshot(input_path)
        kind, tile_index = resolve_tile_target(
            item=item,
            land=land,
            use_index=use_index,
        )

        if kind == "item":
            if not (0 <= tile_index < len(snapshot.item_tiles)):
                raise FileParseError(
                    f"Item tile index {tile_index} out of range "
                    f"(max index {len(snapshot.item_tiles) - 1})"
                )
            entry = dict(snapshot.item_tiles[tile_index])
            graphic_id = item_graphic_id(tile_index)
        else:
            if not (0 <= tile_index < len(snapshot.land_tiles)):
                raise FileParseError(
                    f"Land tile {tile_index} out of range "
                    f"(max index {_LAND_TILE_COUNT - 1})"
                )
            entry = dict(snapshot.land_tiles[tile_index])
            graphic_id = None

        old_flags = entry["flags"]
        entry["flags"] = apply_flag_patch(
            old_flags,
            add=add_mask,
            remove=remove_mask,
            set_to=set_flags,
        )
        if kind == "item":
            snapshot.item_tiles[tile_index] = entry
        else:
            snapshot.land_tiles[tile_index] = entry

        out_path = output_path or input_path
        TileData.save_snapshot(out_path, snapshot)

        new_flags = entry["flags"]
        return _make_patch_summary(
            kind=kind,
            tile_index=tile_index,
            graphic_id=graphic_id,
            name=entry.get("name", ""),
            old_flags=old_flags,
            new_flags=new_flags,
            output_path=out_path,
            source=input_path,
        )

    @staticmethod
    def _load_csv_rows(csv_path: str) -> List[Dict[str, str]]:
        _metadata, rows = _read_tiledata_csv_rows(csv_path)
        return rows

    @staticmethod
    def _save_csv_rows(csv_path: str, rows: List[Dict[str, str]]) -> None:
        try:
            with open(csv_path, "w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh, fieldnames=CSV_FIELDNAMES, extrasaction="ignore"
                )
                writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            raise FileAccessException(
                f"Cannot write tiledata CSV {csv_path!r}: {exc}"
            ) from exc

    @staticmethod
    def get_tile_info(
        path: str,
        *,
        item: int | None = None,
        land: int | None = None,
        use_index: bool = False,
    ) -> Dict[str, object]:
        """Return a readable summary for one tile from CSV or tiledata.mul."""
        csv_path = path if path.lower().endswith(".csv") else None
        kind, tile_index = resolve_tile_target(
            item=item,
            land=land,
            csv_path=csv_path,
            use_index=use_index,
        )

        if path.lower().endswith(".csv"):
            return TileData._get_tile_info_from_csv_row(path, kind, tile_index)

        return TileData._get_tile_info_by_index(path, kind, tile_index)

    @staticmethod
    def _get_tile_info_from_csv_row(
        csv_path: str, kind: str, tile_index: int
    ) -> Dict[str, object]:
        for row in TileData._load_csv_rows(csv_path):
            if (row.get("kind") or "").strip().lower() != kind:
                continue
            if _csv_int(row.get("id")) != tile_index:
                continue
            row_kind, row_index, entry = _entry_from_csv_row(row)
            info = entry_to_tile_info(row_kind, row_index, entry)
            info["source"] = csv_path
            return info
        raise FileParseError(
            f"{kind} tile index {tile_index} not found in CSV {csv_path!r}"
        )

    @staticmethod
    def _get_tile_info_by_index(
        path: str, kind: str, tile_index: int
    ) -> Dict[str, object]:
        snapshot = TileData.load_snapshot(path)
        tiles = snapshot.item_tiles if kind == "item" else snapshot.land_tiles
        if not (0 <= tile_index < len(tiles)):
            raise FileParseError(
                f"{kind} tile index {tile_index} out of range in {path!r}"
            )
        info = entry_to_tile_info(kind, tile_index, tiles[tile_index])
        info["source"] = path
        return info

    @staticmethod
    def patch_flags_csv(
        csv_path: str,
        output_path: str | None = None,
        *,
        item: int | None = None,
        land: int | None = None,
        add: List[str] | None = None,
        remove: List[str] | None = None,
        set_flags: int | None = None,
        use_index: bool = False,
    ) -> Dict[str, object]:
        """Patch flags for one row in a repo CSV file."""
        kind, tile_index = resolve_tile_target(
            item=item,
            land=land,
            csv_path=csv_path,
            use_index=use_index,
        )
        add_mask = parse_flag_names_list(add or [])
        remove_mask = parse_flag_names_list(remove or [])

        if not add_mask and not remove_mask and set_flags is None:
            raise FileParseError(
                "Nothing to do: provide --add, --remove, or --set"
            )

        rows = TileData._load_csv_rows(csv_path)
        matched = False
        old_flags = 0
        new_flags = 0
        name = ""
        graphic_id = item_graphic_id(tile_index) if kind == "item" else None

        for row in rows:
            if (row.get("kind") or "").strip().lower() != kind:
                continue
            if _csv_int(row.get("id")) != tile_index:
                continue

            old_flags = parse_flags_from_csv_row(row)
            new_flags = apply_flag_patch(
                old_flags,
                add=add_mask,
                remove=remove_mask,
                set_to=set_flags,
            )
            row["flags_hex"] = flags_to_hex(new_flags)
            row["flags_names"] = flags_to_names(new_flags)
            name = (row.get("name") or "").strip()
            matched = True
            break

        if not matched:
            raise FileParseError(
                f"{kind} tile index {tile_index} not found in CSV {csv_path!r}"
            )

        out_path = output_path or csv_path
        TileData._save_csv_rows(out_path, rows)
        return _make_patch_summary(
            kind=kind,
            tile_index=tile_index,
            graphic_id=graphic_id,
            name=name,
            old_flags=old_flags,
            new_flags=new_flags,
            output_path=out_path,
            source=csv_path,
        )

    @staticmethod
    def patch_flags(
        path: str,
        output_path: str | None = None,
        *,
        item: int | None = None,
        land: int | None = None,
        add: List[str] | None = None,
        remove: List[str] | None = None,
        set_flags: int | None = None,
        use_index: bool = False,
    ) -> Dict[str, object]:
        """Patch flags in CSV (default workflow) or tiledata.mul."""
        if path.lower().endswith(".csv"):
            return TileData.patch_flags_csv(
                path,
                output_path,
                item=item,
                land=land,
                add=add,
                remove=remove,
                set_flags=set_flags,
                use_index=use_index,
            )
        return TileData.patch_flags_file(
            path,
            output_path,
            item=item,
            land=land,
            add=add,
            remove=remove,
            set_flags=set_flags,
            use_index=use_index,
        )

    @staticmethod
    def diff_csv_vs_mul(
        csv_path: str,
        mul_path: str,
        *,
        item: int | None = None,
        land: int | None = None,
        max_report: int = 20,
        use_index: bool = False,
    ) -> Dict[str, object]:
        """Compare repo CSV against a client tiledata.mul."""
        if item is not None or land is not None:
            kind, tile_index = resolve_tile_target(
                item=item,
                land=land,
                csv_path=csv_path,
                use_index=use_index,
            )
            csv_info = TileData._get_tile_info_from_csv_row(
                csv_path, kind, tile_index
            )
            mul_info = TileData._get_tile_info_by_index(
                mul_path, kind, tile_index
            )
            same = (
                csv_info["flags"] == mul_info["flags"]
                and csv_info.get("name") == mul_info.get("name")
            )
            return {
                "compared": 1,
                "different": 0 if same else 1,
                "tiles": [{"csv": csv_info, "mul": mul_info, "same": same}],
            }

        csv_snapshot = TileData.import_csv(csv_path)
        mul_snapshot = TileData.load_snapshot(mul_path)
        tiles: List[Dict[str, object]] = []
        different = 0
        compared = 0

        for kind, csv_tiles, mul_tiles in (
            ("land", csv_snapshot.land_tiles, mul_snapshot.land_tiles),
            ("item", csv_snapshot.item_tiles, mul_snapshot.item_tiles),
        ):
            limit = min(len(csv_tiles), len(mul_tiles))
            for tile_index in range(limit):
                compared += 1
                csv_entry = csv_tiles[tile_index]
                mul_entry = mul_tiles[tile_index]
                same = (
                    csv_entry.get("flags") == mul_entry.get("flags")
                    and csv_entry.get("name") == mul_entry.get("name")
                )
                if not same:
                    different += 1
                    if len(tiles) < max_report:
                        tiles.append(
                            {
                                "kind": kind,
                                "tile_index": tile_index,
                                "graphic_id": (
                                    item_graphic_id(tile_index)
                                    if kind == "item"
                                    else None
                                ),
                                "csv": entry_to_tile_info(
                                    kind, tile_index, csv_entry
                                ),
                                "mul": entry_to_tile_info(
                                    kind, tile_index, mul_entry
                                ),
                                "same": False,
                            }
                        )

        return {
            "compared": compared,
            "different": different,
            "tiles": tiles,
        }
