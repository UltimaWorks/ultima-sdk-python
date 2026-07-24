"""Ultima Online SDK package initializer.

Exposes common classes at package level for convenience.
All imports are guarded so that missing optional dependencies or missing
client data files do not prevent the package from loading.
"""

__version__ = "1.1.4"
__author__  = "UltimaWorks"

# ---------------------------------------------------------------------------
# Explicit guarded exports: (module_dotted_path, class_name)
# ---------------------------------------------------------------------------
_EXPORTS = [
    ("ultima_sdk.files",          "Files"),
    ("ultima_sdk.tiledata",       "TileData"),
    ("ultima_sdk.hues",           "Hues"),
    ("ultima_sdk.art",            "Art"),
    ("ultima_sdk.animations",     "Animations"),
    ("ultima_sdk.animation_edit", "AnimationEdit"),
    ("ultima_sdk.gumps",          "Gumps"),
    ("ultima_sdk.light",          "Light"),
    ("ultima_sdk.textures",       "Textures"),
    ("ultima_sdk.multis",         "Multis"),
    ("ultima_sdk.map",            "Map"),
    ("ultima_sdk.tile_matrix",    "TileMatrix"),
    ("ultima_sdk.radar_col",      "RadarCol"),
    ("ultima_sdk.file_index",     "FileIndex"),
    ("ultima_sdk.verdata",        "Verdata"),
    ("ultima_sdk.uop",            "UopFile"),
    ("ultima_sdk.uop",            "UopBackedIndex"),
    ("ultima_sdk.uop",            "create_hash"),
    ("ultima_sdk.exceptions",     "FileAccessException"),
    ("ultima_sdk.exceptions",     "FileParseError"),
    ("ultima_sdk.binary_extensions", "BinaryReader"),
    ("ultima_sdk.cliloc",            "Cliloc"),
    ("ultima_sdk.cliloc",            "ClilocEntry"),
]

_exported: list[str] = []
for _mod, _name in _EXPORTS:
    try:
        import importlib as _importlib
        _module = _importlib.import_module(_mod)
        globals()[_name] = getattr(_module, _name)
        _exported.append(_name)
    except Exception:
        pass  # defer until explicitly imported

__all__ = _exported  # pyright: ignore[reportUnsupportedDunderAll]
