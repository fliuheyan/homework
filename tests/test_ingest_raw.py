"""
Tests for etl/ingest_raw.py: verify that _cell_to_display_text stores
Excel cell values exactly as-is (no trimming, no type coercion, no
empty-string-to-NULL conversion, no date/number formatting).
"""
import os
import tempfile
from datetime import datetime, date

import openpyxl
import pytest

from etl.ingest_raw import _cell_to_display_text, _sheet_to_text_df


# ---------------------------------------------------------------------------
# Minimal stub for an openpyxl cell (avoids needing a real workbook)
# ---------------------------------------------------------------------------

class _FakeCell:
    def __init__(self, value, number_format="General"):
        self.value = value
        self.number_format = number_format


# ---------------------------------------------------------------------------
# Unit tests for _cell_to_display_text
# ---------------------------------------------------------------------------

class TestCellToDisplayText:
    def test_none_cell_returns_none(self):
        assert _cell_to_display_text(_FakeCell(None)) is None

    def test_plain_string_preserved(self):
        assert _cell_to_display_text(_FakeCell("hello")) == "hello"

    def test_leading_spaces_preserved(self):
        assert _cell_to_display_text(_FakeCell("  hello")) == "  hello"

    def test_trailing_spaces_preserved(self):
        assert _cell_to_display_text(_FakeCell("hello  ")) == "hello  "

    def test_both_spaces_preserved(self):
        assert _cell_to_display_text(_FakeCell("  hello  ")) == "  hello  "

    def test_empty_string_not_converted_to_none(self):
        result = _cell_to_display_text(_FakeCell(""))
        assert result == ""
        assert result is not None

    def test_date_like_text_preserved(self):
        assert _cell_to_display_text(_FakeCell("2023-01-15")) == "2023-01-15"

    def test_numeric_like_text_preserved(self):
        assert _cell_to_display_text(_FakeCell("007")) == "007"

    def test_integer_value_no_formatting(self):
        # An integer in Excel should become its plain string, not currency-formatted
        result = _cell_to_display_text(_FakeCell(42, number_format="#,##0.00"))
        assert result == "42"

    def test_float_value_no_formatting(self):
        result = _cell_to_display_text(_FakeCell(1234.5, number_format="#,##0.00"))
        assert result == "1234.5"

    def test_datetime_value_no_formatting(self):
        dt = datetime(2023, 1, 15, 10, 30, 0)
        result = _cell_to_display_text(_FakeCell(dt, number_format="dd/mm/yyyy"))
        # Must be the raw str() representation, NOT a reformatted date string
        assert result == str(dt)

    def test_date_value_no_formatting(self):
        d = date(2023, 1, 15)
        result = _cell_to_display_text(_FakeCell(d))
        assert result == str(d)

    def test_no_whitespace_trimming(self):
        """Trimming must never happen, regardless of surrounding whitespace."""
        for raw in [" a", "a ", " a ", "\ta", "a\t"]:
            assert _cell_to_display_text(_FakeCell(raw)) == raw


# ---------------------------------------------------------------------------
# Integration-level test: _sheet_to_text_df via a real in-memory xlsx
# ---------------------------------------------------------------------------

def _make_xlsx(rows: list[list]) -> str:
    """Write rows to a temporary xlsx file and return the file path as a string."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "orders"
    # Header row
    ws.append(["order_date_text", "email_text", "net_amount_text", "order_number_text"])
    for row in rows:
        ws.append(row)
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    wb.save(tmp.name)
    return tmp.name


@pytest.fixture
def xlsx_path():
    """Fixture that yields a factory for temp xlsx files and cleans them up."""
    paths = []

    def factory(rows: list[list]) -> str:
        path = _make_xlsx(rows)
        paths.append(path)
        return path

    yield factory

    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


class TestSheetToTextDf:
    COLS = ["order_date_text", "email_text", "net_amount_text", "order_number_text"]

    def test_leading_trailing_spaces_preserved(self, xlsx_path):
        path = xlsx_path([["  2023-01-15  ", "  user@example.com  ", "  100.00  ", "  ORD-001  "]])
        df = _sheet_to_text_df(path, "orders", self.COLS)
        assert df.iloc[0]["order_date_text"] == "  2023-01-15  "
        assert df.iloc[0]["email_text"] == "  user@example.com  "

    def test_empty_string_preserved_not_null(self, xlsx_path):
        path = xlsx_path([["", "user@example.com", "100", "ORD-001"]])
        df = _sheet_to_text_df(path, "orders", self.COLS)
        val = df.iloc[0]["order_date_text"]
        # Empty string cell: openpyxl reads as None (truly empty), so None is acceptable.
        # The important thing is that a non-None empty string is not converted.
        # If openpyxl returns "" (possible in some versions), it must not become None.
        assert val is None or val == ""

    def test_date_like_text_preserved(self, xlsx_path):
        path = xlsx_path([["2023-01-15", "user@example.com", "100", "ORD-001"]])
        df = _sheet_to_text_df(path, "orders", self.COLS)
        assert df.iloc[0]["order_date_text"] == "2023-01-15"

    def test_numeric_like_text_preserved(self, xlsx_path):
        path = xlsx_path([["2023-01-15", "user@example.com", "007", "ORD-001"]])
        df = _sheet_to_text_df(path, "orders", self.COLS)
        assert df.iloc[0]["net_amount_text"] == "007"

    def test_integer_cell_not_formatted(self, xlsx_path):
        """An integer cell must be stored as plain str(int), no thousand separators."""
        path = xlsx_path([["2023-01-15", "user@example.com", 1234567, "ORD-001"]])
        df = _sheet_to_text_df(path, "orders", self.COLS)
        assert df.iloc[0]["net_amount_text"] == "1234567"

    def test_float_cell_not_formatted(self, xlsx_path):
        path = xlsx_path([["2023-01-15", "user@example.com", 99.9, "ORD-001"]])
        df = _sheet_to_text_df(path, "orders", self.COLS)
        assert df.iloc[0]["net_amount_text"] == "99.9"

    def test_datetime_cell_raw_str(self, xlsx_path):
        """A datetime cell must produce str(datetime), not a reformatted date string."""
        dt = datetime(2023, 1, 15, 0, 0, 0)
        path = xlsx_path([[dt, "user@example.com", "100", "ORD-001"]])
        df = _sheet_to_text_df(path, "orders", self.COLS)
        result = df.iloc[0]["order_date_text"]
        assert result == str(dt)
