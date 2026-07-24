"""Tests for the ultima-cliloc CLI."""

import struct
import subprocess
import sys

import pytest

from ultima_sdk.cliloc_cli import main


def _make_cliloc_bytes(entries: dict[int, str]) -> bytes:
    out = bytearray(struct.pack("<ih", 2, 0))
    for number, text in entries.items():
        payload = text.encode("utf-8")
        out += struct.pack("<iBH", number, 0, len(payload))
        out += payload
    return bytes(out)


def test_cli_to_csv_and_from_csv_round_trip(tmp_path: pytest.FixtureRequest) -> None:
    src = tmp_path / "cliloc.enu"
    src.write_bytes(_make_cliloc_bytes({1: "one", 2: "two"}))

    csv_path = tmp_path / "out.csv"
    dst = tmp_path / "out.enu"

    assert main(["to-csv", str(src), str(csv_path)]) == 0
    assert csv_path.exists()

    assert main(["from-csv", str(csv_path), str(dst)]) == 0
    assert dst.exists()

    reparsed = dst.read_bytes()
    assert reparsed == _make_cliloc_bytes({1: "one", 2: "two"})


def test_cli_module_invocation(tmp_path: pytest.FixtureRequest) -> None:
    src = tmp_path / "a.enu"
    src.write_bytes(_make_cliloc_bytes({99: "x"}))
    csv_path = tmp_path / "a.csv"

    result = subprocess.run(
        [sys.executable, "-m", "ultima_sdk.cliloc_cli", "to-csv", str(src), str(csv_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert csv_path.exists()
    assert "99" in csv_path.read_text(encoding="utf-8")
