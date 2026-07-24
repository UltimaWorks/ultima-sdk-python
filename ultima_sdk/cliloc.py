"""Cliloc file parser for Ultima Online localization string tables.

Cliloc files map integer entry numbers to localized UTF-8 strings.
Common files shipped with the UO client:

  cliloc.enu   – English (US)
  cliloc.deu   – German
  cliloc.custom1 / cliloc.custom2 – server-specific overrides

Binary format (uncompressed)
-----------------------------
Header:
  int32   version   – ignored; present for format identification
  int16   unknown   – ignored

Repeated entries (until EOF):
  int32   number    – unique string identifier
  uint8   flag      – reserved, ignored
  int16   length    – byte length of the following UTF-8 payload
  bytes   text      – UTF-8 string of ``length`` bytes

BWT-compressed variant
-----------------------
When ``raw_bytes[3] == 0x8E`` the file is BWT-compressed (a variant
introduced around client version CV_7010400).  This is extremely rare in
practice; the parser raises :class:`~ultima_sdk.exceptions.FileParseError`
when such a file is encountered.

Usage
-----
::

    from ultima_sdk.cliloc import Cliloc

    Cliloc.initialize(path="/uo/cliloc.enu")
    print(Cliloc.get_string(3000432))   # e.g. "Blacksmith"
    print(Cliloc.count())

Stateless helper (no class-level state)::

    entries = Cliloc.load_file("/uo/cliloc.enu")
    # entries is a plain dict[int, str]
"""

from __future__ import annotations

import csv
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, Literal, Mapping, Optional, Union

from .binary_extensions import BinaryReader
from .exceptions import FileAccessException, FileParseError
from .files import Files

# Byte index 3 of the raw file buffer equal to this value signals BWT compression.
_BWT_MARKER: int = 0x8E

# Known header sizes to probe when the layout is uncertain.
_HEADER_CANDIDATES: tuple[int, ...] = (6, 4, 0)

HeaderStyle = Literal["standard", "v4", "none"]

# CSV columns for round-trip editing (``flag`` is optional on import).
CSV_FIELDNAMES: tuple[str, ...] = ("number", "flag", "text")


@dataclass(frozen=True)
class ClilocEntry:
    """A single, immutable cliloc string entry.

    Attributes:
        number: The numeric identifier of the entry (e.g. ``3000432``).
        flag:   The raw flag byte stored in the file (always ``0`` in practice).
        text:   The decoded UTF-8 string.
    """

    number: int
    flag: int
    text: str


class Cliloc:
    """Static class for loading and querying Ultima Online cliloc localization files.

    The class maintains a single, process-wide cache.  Call :meth:`reset` to
    clear it and allow re-initialization from a different file.

    Example::

        from ultima_sdk.cliloc import Cliloc

        # Auto-discover from the UO client directory configured via Files.
        Cliloc.initialize()

        # Or point directly at a file.
        Cliloc.initialize(path="/uo/cliloc.enu")

        text = Cliloc.get_string(1019548)  # "You have died."
    """

    _entries: Dict[int, ClilocEntry] = {}
    _initialized: bool = False
    _source_path: Optional[str] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def initialize(
        cls,
        path: Optional[str] = None,
        language: str = "enu",
    ) -> bool:
        """Load cliloc data into the class-level cache.

        Args:
            path:     Explicit path to a cliloc file.  When *None* the file is
                      located via :class:`~ultima_sdk.files.Files` using the
                      ``language`` argument.
            language: Language suffix used for auto-discovery (e.g. ``"enu"``
                      resolves to ``cliloc.enu``).  Ignored when *path* is
                      given explicitly.

        Returns:
            ``True`` when data was loaded successfully or the class was already
            initialized for the same source.  ``True`` is also returned when no
            cliloc file is discoverable (entries will simply be empty).

        Raises:
            FileAccessException: When *path* is given explicitly but the file
                                 does not exist or cannot be read.
            FileParseError:      When the file content cannot be parsed.
        """
        if cls._initialized:
            if path is None and cls._source_path is not None:
                return True
            if path is not None and cls._source_path == path:
                return True

        try:
            resolved = cls._resolve_path(path, language)
        except FileAccessException:
            raise

        if resolved is None:
            cls._entries = {}
            cls._source_path = None
            cls._initialized = True
            return True

        try:
            with open(resolved, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            raise FileAccessException(
                f"Cannot read cliloc file {resolved!r}: {exc}"
            ) from exc

        try:
            cls._entries = cls.parse_entries(raw)
        except FileParseError:
            raise
        except Exception as exc:
            raise FileParseError(
                f"Failed to parse cliloc file {resolved!r}: {exc}"
            ) from exc

        cls._source_path = resolved
        cls._initialized = True
        return True

    @classmethod
    def reset(cls) -> None:
        """Clear all loaded data and allow re-initialization.

        Useful in tests or when switching between language files.
        """
        cls._entries = {}
        cls._initialized = False
        cls._source_path = None

    # ------------------------------------------------------------------
    # Query interface
    # ------------------------------------------------------------------

    @classmethod
    def get_string(cls, number: int) -> Optional[str]:
        """Return the localized string for *number*, or ``None`` if absent.

        Triggers lazy initialization if the class has not been initialized yet.
        """
        if not cls._initialized:
            cls.initialize()
        entry = cls._entries.get(number)
        return entry.text if entry is not None else None

    @classmethod
    def contains(cls, number: int) -> bool:
        """Return ``True`` if *number* exists in the loaded entries."""
        if not cls._initialized:
            cls.initialize()
        return number in cls._entries

    @classmethod
    def count(cls) -> int:
        """Return the total number of loaded entries."""
        if not cls._initialized:
            cls.initialize()
        return len(cls._entries)

    @classmethod
    def all_entries(cls) -> Dict[int, str]:
        """Return a shallow copy of the full ``{number: text}`` mapping."""
        if not cls._initialized:
            cls.initialize()
        return {number: entry.text for number, entry in cls._entries.items()}

    @classmethod
    def iter_entries(cls) -> Iterator[ClilocEntry]:
        """Yield all loaded entries as :class:`ClilocEntry` objects."""
        if not cls._initialized:
            cls.initialize()
        yield from cls._entries.values()

    # ------------------------------------------------------------------
    # Stateless helpers (no class-level side effects)
    # ------------------------------------------------------------------

    @staticmethod
    def load_file(path: str) -> Dict[int, str]:
        """Load a cliloc file and return ``{number: text}`` without caching.

        This is a stateless helper that does not affect the class-level cache.

        Args:
            path: Absolute or relative path to any cliloc file.

        Returns:
            Dictionary mapping entry numbers to decoded strings.

        Raises:
            FileAccessException: If the file cannot be opened.
            FileParseError:      If the content cannot be parsed.
        """
        return {
            number: entry.text
            for number, entry in Cliloc.load_entries(path).items()
        }

    @staticmethod
    def load_entries(path: str) -> Dict[int, ClilocEntry]:
        """Load a cliloc file and return full entries (including flags).

        Raises:
            FileAccessException: If the file cannot be opened.
            FileParseError:      If the content cannot be parsed.
        """
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            raise FileAccessException(
                f"Cannot read cliloc file {path!r}: {exc}"
            ) from exc

        return Cliloc.parse_entries(raw)

    @staticmethod
    def parse_bytes(data: bytes) -> Dict[int, str]:
        """Parse raw cliloc bytes and return ``{number: text}``.

        See :meth:`parse_entries` for format details and error conditions.
        """
        return {
            number: entry.text
            for number, entry in Cliloc.parse_entries(data).items()
        }

    @staticmethod
    def parse_entries(data: bytes) -> Dict[int, ClilocEntry]:
        """Parse raw cliloc bytes and return full :class:`ClilocEntry` objects.

        Raises:
            FileParseError: If *data* is empty, BWT-compressed, or cannot be
                            parsed with any known header layout.
        """
        if not data:
            raise FileParseError("Empty cliloc data")

        if len(data) > 3 and data[3] == _BWT_MARKER:
            raise FileParseError(
                "BWT-compressed cliloc files (byte[3] == 0x8E) are not supported. "
                "This variant appears in very late UO clients (CV_7010400+). "
                "Provide a decompressed copy of the file."
            )

        last_error: Exception = ValueError("No header candidate attempted")
        for skip in _HEADER_CANDIDATES:
            try:
                return Cliloc._parse_entries(data, skip)
            except Exception as exc:
                last_error = exc

        raise FileParseError(
            "Unable to parse cliloc data – file may be corrupt or in an "
            "unrecognised format."
        ) from last_error

    # ------------------------------------------------------------------
    # CSV and binary conversion
    # ------------------------------------------------------------------

    @staticmethod
    def export_csv(
        csv_path: str,
        entries: Optional[Mapping[int, Union[ClilocEntry, str]]] = None,
        *,
        sort_by_number: bool = True,
    ) -> int:
        """Write cliloc entries to a UTF-8 CSV file.

        Columns: ``number``, ``flag``, ``text``. Suitable for editing in Excel,
        LibreOffice, or any text editor.

        Args:
            csv_path:        Output CSV path.
            entries:         Entries to export. When *None*, uses the class
                             cache (calls :meth:`initialize` if needed).
            sort_by_number:  Sort rows by entry number before writing.

        Returns:
            Number of rows written.

        Raises:
            FileAccessException: If the output file cannot be written.
            FileParseError:      If *entries* is empty.
        """
        if entries is None:
            if not Cliloc._initialized:
                Cliloc.initialize()
            entries = Cliloc._entries

        normalized = Cliloc._normalize_entries(entries)
        if not normalized:
            raise FileParseError("No cliloc entries to export")

        rows = sorted(normalized.values(), key=lambda e: e.number)
        if not sort_by_number:
            rows = list(normalized.values())

        try:
            with open(csv_path, "w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
                writer.writeheader()
                for entry in rows:
                    writer.writerow(
                        {
                            "number": entry.number,
                            "flag": entry.flag,
                            "text": entry.text,
                        }
                    )
        except OSError as exc:
            raise FileAccessException(
                f"Cannot write cliloc CSV {csv_path!r}: {exc}"
            ) from exc

        return len(rows)

    @staticmethod
    def import_csv(csv_path: str) -> Dict[int, ClilocEntry]:
        """Load cliloc entries from a CSV file.

        Expects columns ``number`` and ``text``. Column ``flag`` is optional
        (defaults to ``0``). Extra columns are ignored.

        Raises:
            FileAccessException: If the file cannot be read.
            FileParseError:      On invalid rows or duplicate entry numbers.
        """
        try:
            with open(csv_path, encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                if reader.fieldnames is None:
                    raise FileParseError(f"Empty CSV file: {csv_path!r}")

                fields = {name.strip().lower() for name in reader.fieldnames}
                if "number" not in fields or "text" not in fields:
                    raise FileParseError(
                        f"CSV must contain 'number' and 'text' columns, "
                        f"got: {reader.fieldnames!r}"
                    )

                entries: Dict[int, ClilocEntry] = {}
                for line_no, row in enumerate(reader, start=2):
                    number_raw = (row.get("number") or "").strip()
                    if not number_raw:
                        continue

                    try:
                        number = int(number_raw, 0)
                    except ValueError as exc:
                        raise FileParseError(
                            f"{csv_path!r} line {line_no}: invalid number "
                            f"{number_raw!r}"
                        ) from exc

                    if number < 0:
                        raise FileParseError(
                            f"{csv_path!r} line {line_no}: negative number {number}"
                        )

                    flag_raw = (row.get("flag") or "0").strip()
                    try:
                        flag = int(flag_raw, 0) if flag_raw else 0
                    except ValueError as exc:
                        raise FileParseError(
                            f"{csv_path!r} line {line_no}: invalid flag {flag_raw!r}"
                        ) from exc

                    if not 0 <= flag <= 255:
                        raise FileParseError(
                            f"{csv_path!r} line {line_no}: flag must be 0-255, "
                            f"got {flag}"
                        )

                    text = row.get("text")
                    if text is None:
                        text = ""
                    text = text.rstrip("\x00")

                    if number in entries:
                        raise FileParseError(
                            f"{csv_path!r} line {line_no}: duplicate entry number "
                            f"{number}"
                        )

                    entries[number] = ClilocEntry(
                        number=number, flag=flag, text=text
                    )

        except OSError as exc:
            raise FileAccessException(
                f"Cannot read cliloc CSV {csv_path!r}: {exc}"
            ) from exc

        if not entries:
            raise FileParseError(f"No cliloc entries found in CSV: {csv_path!r}")

        return entries

    @staticmethod
    def build_bytes(
        entries: Mapping[int, Union[ClilocEntry, str]],
        *,
        version: int = 2,
        unknown: int = 0,
        header: HeaderStyle = "standard",
        sort_entries: bool = True,
    ) -> bytes:
        """Serialize entries to uncompressed cliloc binary bytes.

        Args:
            entries:       Mapping of entry number to :class:`ClilocEntry` or
                           plain text (flag ``0`` is used for strings).
            version:       ``int32`` header version (``header='standard'`` or
                           ``'v4'`` only).
            unknown:       ``int16`` header field (``header='standard'`` only).
            header:        ``'standard'`` (6-byte header), ``'v4'`` (4-byte), or
                           ``'none'``.
            sort_entries:  Write entries sorted by number (recommended).

        Raises:
            FileParseError: If *entries* is empty or a string exceeds 32767 bytes.
        """
        normalized = Cliloc._normalize_entries(entries)
        if not normalized:
            raise FileParseError("No cliloc entries to serialize")

        out = bytearray()
        if header == "standard":
            out.extend(struct.pack("<ih", version, unknown))
        elif header == "v4":
            out.extend(struct.pack("<i", version))
        elif header == "none":
            pass
        else:
            raise FileParseError(f"Unknown cliloc header style: {header!r}")

        items = normalized.values()
        if sort_entries:
            items = sorted(items, key=lambda e: e.number)

        for entry in items:
            payload = entry.text.encode("utf-8")
            if len(payload) > 0x7FFF:
                raise FileParseError(
                    f"Cliloc entry {entry.number} text exceeds maximum length "
                    f"({len(payload)} > 32767 bytes)"
                )
            out.extend(struct.pack("<iBH", entry.number, entry.flag, len(payload)))
            out.extend(payload)

        return bytes(out)

    @staticmethod
    def save_file(
        path: str,
        entries: Optional[Mapping[int, Union[ClilocEntry, str]]] = None,
        **build_kwargs: object,
    ) -> None:
        """Write cliloc binary bytes to *path*.

        Args:
            path:    Output file path.
            entries: Entries to write. When *None*, uses the class cache.
            **build_kwargs: Forwarded to :meth:`build_bytes`.
        """
        if entries is None:
            if not Cliloc._initialized:
                Cliloc.initialize()
            entries = Cliloc._entries

        data = Cliloc.build_bytes(entries, **build_kwargs)  # type: ignore[arg-type]

        try:
            with open(path, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            raise FileAccessException(
                f"Cannot write cliloc file {path!r}: {exc}"
            ) from exc

    @staticmethod
    def convert_to_csv(cliloc_path: str, csv_path: str, **export_kwargs: object) -> int:
        """Convert a cliloc binary file to CSV. Returns the number of rows written."""
        entries = Cliloc.load_entries(cliloc_path)
        return Cliloc.export_csv(
            csv_path, entries, **export_kwargs  # type: ignore[arg-type]
        )

    @staticmethod
    def convert_from_csv(
        csv_path: str,
        cliloc_path: str,
        **build_kwargs: object,
    ) -> int:
        """Convert a CSV file to cliloc binary. Returns the number of entries written."""
        entries = Cliloc.import_csv(csv_path)
        Cliloc.save_file(cliloc_path, entries, **build_kwargs)
        return len(entries)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_entries(
        entries: Mapping[int, Union[ClilocEntry, str]],
    ) -> Dict[int, ClilocEntry]:
        """Convert a mixed mapping to ``{number: ClilocEntry}``."""
        out: Dict[int, ClilocEntry] = {}
        for key, value in entries.items():
            if isinstance(value, ClilocEntry):
                if value.number != key:
                    out[key] = ClilocEntry(
                        number=key, flag=value.flag, text=value.text
                    )
                else:
                    out[key] = value
            else:
                out[int(key)] = ClilocEntry(number=int(key), flag=0, text=value)
        return out

    @staticmethod
    def _parse_entries(data: bytes, header_skip: int) -> Dict[int, ClilocEntry]:
        """Parse cliloc entry records starting after *header_skip* bytes.

        Args:
            data:        Raw bytes of the full file.
            header_skip: Number of bytes to skip before reading entries.

        Returns:
            Non-empty dictionary mapping entry numbers to entries.

        Raises:
            ValueError: On malformed records (negative number, bad length).
            EOFError:   On truncated data mid-record.
        """
        reader = BinaryReader(data)
        if header_skip:
            reader.seek(header_skip)

        entries: Dict[int, ClilocEntry] = {}
        while True:
            try:
                number = reader.read_int32()
            except EOFError:
                break

            if number < 0:
                raise ValueError(f"Negative cliloc entry number: {number}")

            flag = reader.read_byte()
            length = reader.read_uint16()

            text_bytes = reader.read(length)
            if len(text_bytes) != length:
                raise EOFError(
                    f"Truncated cliloc string payload at entry {number}: "
                    f"expected {length} bytes, got {len(text_bytes)}"
                )

            text = text_bytes.decode("utf-8", errors="replace").rstrip("\x00")
            entries[number] = ClilocEntry(number=number, flag=flag, text=text)

        if not entries:
            raise ValueError("No cliloc entries found")

        return entries

    @staticmethod
    def _resolve_path(explicit_path: Optional[str], language: str) -> Optional[str]:
        """Resolve which cliloc file to load.

        When *explicit_path* is given it is validated and returned directly.
        Otherwise :class:`~ultima_sdk.files.Files` is queried for
        ``cliloc.<language>`` and common fallback names.

        Returns:
            Absolute path string, or ``None`` when no file can be found.

        Raises:
            FileAccessException: When *explicit_path* is provided but absent.
        """
        if explicit_path is not None:
            if not Path(explicit_path).exists():
                raise FileAccessException(
                    f"Cliloc file not found: {explicit_path!r}"
                )
            return explicit_path

        # Build candidate list: preferred language first, then common fallbacks.
        candidates: list[str] = [f"cliloc.{language}"]
        for fallback in ("cliloc.enu", "cliloc.deu", "cliloc.custom1", "cliloc.custom2"):
            if fallback not in candidates:
                candidates.append(fallback)

        for name in candidates:
            p = Files.get_file_path(name)
            if p:
                return p

        return None
