"""Tests for the Cliloc parser (ultima_sdk.cliloc)."""

import struct

import pytest

from ultima_sdk.cliloc import Cliloc, ClilocEntry
from ultima_sdk.exceptions import FileAccessException, FileParseError
from ultima_sdk.files import Files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cliloc_bytes(
    entries: dict[int, str], *, header: str = "standard"
) -> bytes:
    """Build minimal syntactically-valid cliloc bytes for testing.

    Args:
        entries: Mapping of ``{number: text}`` to encode.
        header:  One of ``"standard"`` (int32 + int16 = 6 bytes),
                 ``"v4"`` (int32 = 4 bytes), or ``"none"`` (no header).
    """
    if header == "standard":
        out = bytearray(struct.pack("<ih", 2, 0))
    elif header == "v4":
        out = bytearray(struct.pack("<i", 2))
    elif header == "none":
        out = bytearray()
    else:
        raise ValueError(f"Unknown header variant: {header!r}")

    for number, text in entries.items():
        payload = text.encode("utf-8")
        out += struct.pack("<iBH", number, 0, len(payload))
        out += payload

    return bytes(out)


# ---------------------------------------------------------------------------
# Cliloc.parse_bytes – stateless unit tests
# ---------------------------------------------------------------------------


class TestClilocParseBytes:
    def test_standard_header(self) -> None:
        data = _make_cliloc_bytes({1000: "Hello", 1001: "World"})
        result = Cliloc.parse_bytes(data)
        assert result == {1000: "Hello", 1001: "World"}

    def test_v4_header(self) -> None:
        data = _make_cliloc_bytes({42: "Test"}, header="v4")
        result = Cliloc.parse_bytes(data)
        assert result == {42: "Test"}

    def test_no_header(self) -> None:
        data = _make_cliloc_bytes({99: "X"}, header="none")
        result = Cliloc.parse_bytes(data)
        assert result == {99: "X"}

    def test_empty_data_raises(self) -> None:
        with pytest.raises(FileParseError):
            Cliloc.parse_bytes(b"")

    def test_bwt_compressed_raises(self) -> None:
        """Byte[3] == 0x8E must raise FileParseError mentioning BWT."""
        data = bytearray(_make_cliloc_bytes({1: "hi"}))
        data[3] = 0x8E
        with pytest.raises(FileParseError, match="BWT"):
            Cliloc.parse_bytes(bytes(data))

    def test_unicode_text(self) -> None:
        data = _make_cliloc_bytes({500: "Złoto i srebro"})
        result = Cliloc.parse_bytes(data)
        assert result[500] == "Złoto i srebro"

    def test_empty_string_entry(self) -> None:
        data = _make_cliloc_bytes({0: ""})
        result = Cliloc.parse_bytes(data)
        assert result[0] == ""

    def test_multiple_entries_preserve_all(self) -> None:
        source = {i: f"string_{i}" for i in range(20)}
        data = _make_cliloc_bytes(source)
        result = Cliloc.parse_bytes(data)
        assert result == source

    def test_large_entry_number(self) -> None:
        data = _make_cliloc_bytes({2_000_000: "far out"})
        result = Cliloc.parse_bytes(data)
        assert result[2_000_000] == "far out"


# ---------------------------------------------------------------------------
# Cliloc.load_file – stateless file helper
# ---------------------------------------------------------------------------


class TestClilocLoadFile:
    def test_loads_from_disk(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({100: "hundred"}))
        result = Cliloc.load_file(str(p))
        assert result == {100: "hundred"}

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileAccessException):
            Cliloc.load_file("/nonexistent/cliloc.enu")

    def test_does_not_affect_class_state(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({1: "a"}))
        Cliloc.reset()
        Cliloc.load_file(str(p))
        assert not Cliloc._initialized


# ---------------------------------------------------------------------------
# Cliloc class-level API
# ---------------------------------------------------------------------------


class TestClilocClass:
    def setup_method(self) -> None:
        Cliloc.reset()

    # --- initialize ---

    def test_initialize_returns_true(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({1000: "Hello"}))
        assert Cliloc.initialize(path=str(p)) is True

    def test_initialize_idempotent_same_path(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({1: "a"}))
        assert Cliloc.initialize(path=str(p)) is True
        assert Cliloc.initialize(path=str(p)) is True
        assert Cliloc.count() == 1

    def test_initialize_explicit_path_missing_raises(self) -> None:
        with pytest.raises(FileAccessException):
            Cliloc.initialize(path="/definitely/does/not/exist.enu")

    def test_initialize_no_file_returns_true_empty(self, tmp_path: pytest.FixtureRequest) -> None:
        # Empty directory – no cliloc found.
        Files.set_directory(str(tmp_path))
        assert Cliloc.initialize() is True
        assert Cliloc.count() == 0

    # --- get_string ---

    def test_get_string_found(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({1000: "Hello"}))
        Cliloc.initialize(path=str(p))
        assert Cliloc.get_string(1000) == "Hello"

    def test_get_string_missing_returns_none(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({1: "a"}))
        Cliloc.initialize(path=str(p))
        assert Cliloc.get_string(9999) is None

    def test_get_string_triggers_lazy_init(self, tmp_path: pytest.FixtureRequest) -> None:
        # Create the file before pointing Files at the directory so the
        # directory scan picks it up immediately.
        (tmp_path / "cliloc.enu").write_bytes(_make_cliloc_bytes({7: "lazy"}))
        Files.set_directory(str(tmp_path))
        Cliloc.reset()
        assert not Cliloc._initialized
        assert Cliloc.get_string(7) == "lazy"
        assert Cliloc._initialized

    # --- contains ---

    def test_contains_present(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({5: "X"}))
        Cliloc.initialize(path=str(p))
        assert Cliloc.contains(5) is True

    def test_contains_absent(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({5: "X"}))
        Cliloc.initialize(path=str(p))
        assert Cliloc.contains(999) is False

    # --- count ---

    def test_count(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({1: "a", 2: "b", 3: "c"}))
        Cliloc.initialize(path=str(p))
        assert Cliloc.count() == 3

    # --- all_entries ---

    def test_all_entries_is_copy(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({10: "ten", 20: "twenty"}))
        Cliloc.initialize(path=str(p))
        entries = Cliloc.all_entries()
        assert entries == {10: "ten", 20: "twenty"}
        # Mutation of the returned dict must not affect class state.
        entries[10] = "modified"
        assert Cliloc.get_string(10) == "ten"

    # --- iter_entries ---

    def test_iter_entries_yields_cliloc_entry(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({7: "seven"}))
        Cliloc.initialize(path=str(p))
        all_entries = list(Cliloc.iter_entries())
        assert len(all_entries) == 1
        entry = all_entries[0]
        assert isinstance(entry, ClilocEntry)
        assert entry.number == 7
        assert entry.text == "seven"
        assert entry.flag == 0

    def test_iter_entries_count_matches(self, tmp_path: pytest.FixtureRequest) -> None:
        source = {i: f"text_{i}" for i in range(50)}
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes(source))
        Cliloc.initialize(path=str(p))
        assert len(list(Cliloc.iter_entries())) == 50

    # --- reset ---

    def test_reset_clears_state(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({1: "a"}))
        Cliloc.initialize(path=str(p))
        assert Cliloc.count() == 1
        Cliloc.reset()
        assert not Cliloc._initialized
        assert Cliloc._source_path is None

    # --- auto-discovery via Files ---

    def test_auto_discover_enu(self, tmp_path: pytest.FixtureRequest) -> None:
        (tmp_path / "cliloc.enu").write_bytes(
            _make_cliloc_bytes({42: "auto-discovered"})
        )
        Files.set_directory(str(tmp_path))
        assert Cliloc.initialize() is True
        assert Cliloc.get_string(42) == "auto-discovered"

    def test_language_selection_deu(self, tmp_path: pytest.FixtureRequest) -> None:
        (tmp_path / "cliloc.deu").write_bytes(_make_cliloc_bytes({1: "Hallo"}))
        Files.set_directory(str(tmp_path))
        assert Cliloc.initialize(language="deu") is True
        assert Cliloc.get_string(1) == "Hallo"

    def test_fallback_to_enu_when_language_missing(
        self, tmp_path: pytest.FixtureRequest
    ) -> None:
        # Only cliloc.enu is present; requesting "deu" should fall back.
        (tmp_path / "cliloc.enu").write_bytes(_make_cliloc_bytes({99: "fallback"}))
        Files.set_directory(str(tmp_path))
        assert Cliloc.initialize(language="deu") is True
        assert Cliloc.get_string(99) == "fallback"

    # --- header variants ---

    def test_v4_header_via_initialize(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({55: "v4"}, header="v4"))
        Cliloc.initialize(path=str(p))
        assert Cliloc.get_string(55) == "v4"

    def test_no_header_via_initialize(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({77: "bare"}, header="none"))
        Cliloc.initialize(path=str(p))
        assert Cliloc.get_string(77) == "bare"


# ---------------------------------------------------------------------------
# ClilocEntry dataclass
# ---------------------------------------------------------------------------


class TestClilocCsvConversion:
    def test_export_and_import_csv(self, tmp_path: pytest.FixtureRequest) -> None:
        source = {1000: "Hello", 1001: 'Say "hi"'}
        cliloc_path = tmp_path / "cliloc.enu"
        cliloc_path.write_bytes(_make_cliloc_bytes(source))

        csv_path = tmp_path / "cliloc.csv"
        assert Cliloc.convert_to_csv(str(cliloc_path), str(csv_path)) == 2

        imported = Cliloc.import_csv(str(csv_path))
        assert imported[1000].text == "Hello"
        assert imported[1001].text == 'Say "hi"'

    def test_round_trip_binary_csv_binary(self, tmp_path: pytest.FixtureRequest) -> None:
        original = {1: "one", 2: "two", 99: "Złoto"}
        src = tmp_path / "src.enu"
        src.write_bytes(_make_cliloc_bytes(original))

        csv_path = tmp_path / "out.csv"
        dst = tmp_path / "dst.enu"

        Cliloc.convert_to_csv(str(src), str(csv_path))
        Cliloc.convert_from_csv(str(csv_path), str(dst))

        reparsed = Cliloc.load_file(str(dst))
        assert reparsed == original

    def test_build_bytes_matches_helper(self) -> None:
        entries = {42: "answer"}
        built = Cliloc.build_bytes(entries)
        expected = _make_cliloc_bytes(entries)
        assert built == expected

    def test_save_file_writes_disk(self, tmp_path: pytest.FixtureRequest) -> None:
        out = tmp_path / "custom.enu"
        Cliloc.save_file(str(out), {7: "seven"})
        assert Cliloc.load_file(str(out)) == {7: "seven"}

    def test_import_csv_optional_flag_defaults_zero(
        self, tmp_path: pytest.FixtureRequest
    ) -> None:
        csv_path = tmp_path / "minimal.csv"
        csv_path.write_text("number,text\n100,Hello\n", encoding="utf-8")
        entries = Cliloc.import_csv(str(csv_path))
        assert entries[100].flag == 0
        assert entries[100].text == "Hello"

    def test_import_csv_duplicate_number_raises(
        self, tmp_path: pytest.FixtureRequest
    ) -> None:
        csv_path = tmp_path / "dup.csv"
        csv_path.write_text(
            "number,flag,text\n1,a\n1,b\n",
            encoding="utf-8",
        )
        with pytest.raises(FileParseError, match="duplicate"):
            Cliloc.import_csv(str(csv_path))

    def test_export_from_class_cache(self, tmp_path: pytest.FixtureRequest) -> None:
        p = tmp_path / "cliloc.enu"
        p.write_bytes(_make_cliloc_bytes({5: "cached"}))
        Cliloc.reset()
        Cliloc.initialize(path=str(p))

        csv_path = tmp_path / "from_cache.csv"
        assert Cliloc.export_csv(str(csv_path)) == 1
        assert Cliloc.import_csv(str(csv_path))[5].text == "cached"

    def test_preserve_flag_round_trip(self) -> None:
        entries = {10: ClilocEntry(number=10, flag=3, text="flagged")}
        built = Cliloc.build_bytes(entries)
        parsed = Cliloc.parse_entries(built)
        assert parsed[10].flag == 3
        assert parsed[10].text == "flagged"


class TestClilocEntry:
    def test_is_frozen(self) -> None:
        entry = ClilocEntry(number=1, flag=0, text="hello")
        with pytest.raises((AttributeError, TypeError)):
            entry.text = "modified"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = ClilocEntry(number=1, flag=0, text="hello")
        b = ClilocEntry(number=1, flag=0, text="hello")
        assert a == b

    def test_inequality_on_number(self) -> None:
        a = ClilocEntry(number=1, flag=0, text="hello")
        b = ClilocEntry(number=2, flag=0, text="hello")
        assert a != b
