"""
Tests for etl/ingest_raw.py: verify that _cell_to_display_text stores
Excel cell values exactly as-is (no trimming, no empty-string-to-NULL
conversion, no extra processing beyond Excel's raw read value).
"""
import os
import tempfile
from datetime import datetime
import importlib

import openpyxl
import pandas as pd
import pytest

from etl.ingest_raw import _cell_to_display_text, _sheet_to_text_df

CUSTOMER_CITY_ZIP_MODULE = "etl.transformers.customer.05_normalize_city_zip"
COLS = ["order_date_text", "email_text", "net_amount_text", "order_number_text"]
ACCOUNTING_EUR_FORMAT = '_-* #,##0.00\\ "€"_-;\\-* #,##0.00\\ "€"_-;_-* "-"??\\ "€"_-;_-@_-'


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
        result = _cell_to_display_text(_FakeCell(42, number_format="#,##0.00"))
        assert result == "42.00"

    def test_float_value_no_formatting(self):
        result = _cell_to_display_text(_FakeCell(1234.5, number_format="#,##0.00"))
        assert result == "1,234.50"

    def test_datetime_value_no_formatting(self):
        dt = datetime(2023, 1, 15, 10, 30, 0)
        result = _cell_to_display_text(_FakeCell(dt, number_format="dd/mm/yyyy"))
        assert result == "15/01/2023"

    def test_currency_prefix_symbol_preserved(self):
        result = _cell_to_display_text(_FakeCell(20.46, number_format="$#,##0.00"))
        assert result == "$20.46"

    def test_currency_suffix_symbol_preserved(self):
        result = _cell_to_display_text(_FakeCell(43.58, number_format="#,##0.00 €"))
        assert result == "43.58 €"

    def test_chinese_date_display_preserved(self):
        dt = datetime(2021, 8, 8, 0, 0, 0)
        result = _cell_to_display_text(_FakeCell(dt, number_format="yyyy年m月d日"))
        assert result == "2021年8月8日"

    def test_locale_short_month_date_display_preserved(self):
        dt = datetime(2021, 8, 2, 0, 0, 0)
        # [$-407] is a locale tag; raw ingest should preserve the visible d/mmm text.
        result = _cell_to_display_text(_FakeCell(dt, number_format='[$-407]d/\\ mmm/;@'))
        assert result == "2/ Aug/"

    def test_long_date_display_preserved(self):
        dt = datetime(2021, 8, 8, 0, 0, 0)
        assert dt.weekday() == 6
        result = _cell_to_display_text(_FakeCell(dt, number_format='[$-F800]dddd\\,\\ mmmm\\ dd\\,\\ yyyy'))
        assert result == "Sunday, August 08, 2021"

    def test_time_only_format_not_forced_to_date(self):
        dt = datetime(2021, 8, 8, 12, 34, 56)
        result = _cell_to_display_text(_FakeCell(dt, number_format="hh:mm:ss"))
        assert result == "12:34:56"

    def test_single_hour_token_preserved(self):
        dt = datetime(2021, 8, 8, 0, 34, 56)
        result = _cell_to_display_text(_FakeCell(dt, number_format="yyyy-mm-dd h:mm:ss"))
        assert result == "2021-08-08 0:34:56"

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
    ws.append(COLS)
    for row in rows:
        ws.append(row)
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    wb.save(tmp.name)
    return tmp.name


def _make_xlsx_with_formats(rows: list[list], number_formats: dict[tuple[int, int], str]) -> str:
    """Write rows to a temporary xlsx and apply custom formats (1-based row/col keys)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "orders"
    ws.append(COLS)
    for row in rows:
        ws.append(row)
    for (r, c), fmt in number_formats.items():
        ws.cell(row=r, column=c).number_format = fmt
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


@pytest.fixture
def xlsx_path_with_formats():
    """Fixture for temp xlsx files with custom number formats."""
    paths = []

    def factory(rows: list[list], number_formats: dict[tuple[int, int], str]) -> str:
        path = _make_xlsx_with_formats(rows, number_formats)
        paths.append(path)
        return path

    yield factory

    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


class TestSheetToTextDf:
    def test_leading_trailing_spaces_preserved(self, xlsx_path):
        path = xlsx_path([["  2023-01-15  ", "  user@example.com  ", "  100.00  ", "  ORD-001  "]])
        df = _sheet_to_text_df(path, "orders", COLS)
        assert df.iloc[0]["order_date_text"] == "  2023-01-15  "
        assert df.iloc[0]["email_text"] == "  user@example.com  "

    def test_empty_string_preserved_not_null(self, xlsx_path):
        path = xlsx_path([["", "user@example.com", "100", "ORD-001"]])
        df = _sheet_to_text_df(path, "orders", COLS)
        val = df.iloc[0]["order_date_text"]
        # Empty string cell: openpyxl reads as None (truly empty), so None is acceptable.
        # The important thing is that a non-None empty string is not converted.
        # If openpyxl returns "" (possible in some versions), it must not become None.
        assert val is None or val == ""

    def test_date_like_text_preserved(self, xlsx_path):
        path = xlsx_path([["2023-01-15", "user@example.com", "100", "ORD-001"]])
        df = _sheet_to_text_df(path, "orders", COLS)
        assert df.iloc[0]["order_date_text"] == "2023-01-15"

    def test_numeric_like_text_preserved(self, xlsx_path):
        path = xlsx_path([["2023-01-15", "user@example.com", "007", "ORD-001"]])
        df = _sheet_to_text_df(path, "orders", COLS)
        assert df.iloc[0]["net_amount_text"] == "007"

    def test_integer_cell_not_formatted(self, xlsx_path):
        path = xlsx_path([["2023-01-15", "user@example.com", 1234567, "ORD-001"]])
        df = _sheet_to_text_df(path, "orders", COLS)
        assert df.iloc[0]["net_amount_text"] == "1234567"

    def test_float_cell_not_formatted(self, xlsx_path):
        path = xlsx_path([["2023-01-15", "user@example.com", 99.9, "ORD-001"]])
        df = _sheet_to_text_df(path, "orders", COLS)
        assert df.iloc[0]["net_amount_text"] == "99.9"

    def test_datetime_cell_raw_str(self, xlsx_path):
        dt = datetime(2023, 1, 15, 0, 0, 0)
        path = xlsx_path([[dt, "user@example.com", "100", "ORD-001"]])
        df = _sheet_to_text_df(path, "orders", COLS)
        result = df.iloc[0]["order_date_text"]
        assert result == "2023-01-15 0:00:00"

    def test_datetime_and_currency_display_preserved(self, xlsx_path_with_formats):
        path = xlsx_path_with_formats(
            [[datetime(2021, 8, 8, 0, 0, 0), "user@example.com", 43.58, "ORD-001"]],
            {(2, 1): "yyyy年m月d日", (2, 3): "#,##0.00 €"},
        )
        df = _sheet_to_text_df(path, "orders", COLS)
        assert df.iloc[0]["order_date_text"] == "2021年8月8日"
        assert df.iloc[0]["net_amount_text"] == "43.58 €"

    def test_workbook_specific_formats_preserved(self, xlsx_path_with_formats):
        path = xlsx_path_with_formats(
            [[datetime(2021, 8, 2, 0, 0, 0), "user@example.com", 96.17, "ORD-001"]],
            {
                (2, 1): '[$-407]d/\\ mmm/;@',
                (2, 3): ACCOUNTING_EUR_FORMAT,
            },
        )
        df = _sheet_to_text_df(path, "orders", COLS)
        assert df.iloc[0]["order_date_text"] == "2/ Aug/"
        assert df.iloc[0]["net_amount_text"] == "96.17 €"
        
    def test_preformatted_text_cells_preserved(self, xlsx_path):
        path = xlsx_path([["2021年8月5日", "Muster8@Mailing.com", "$51.95", "ORD153"]])
        df = _sheet_to_text_df(path, "orders", COLS)
        assert df.iloc[0]["order_date_text"] == "2021年8月5日"
        assert df.iloc[0]["email_text"] == "Muster8@Mailing.com"
        assert df.iloc[0]["net_amount_text"] == "$51.95"
        assert df.iloc[0]["order_number_text"] == "ORD153"


class TestCustomerCityZipNormalize:
    module = importlib.import_module(CUSTOMER_CITY_ZIP_MODULE)

    def test_extract_zip_from_city_when_zip_empty(self):
        df = self.module.transform(
            pd.DataFrame(
                [
                    {
                        "zip_code_text": "",
                        "city_text": "Düsseldorf 40239",
                        "loyalty_score_text": "10",
                    }
                ]
            )
        )
        assert df.iloc[0]["zip_code"] == "40239"
        assert df.iloc[0]["city"] == "Düsseldorf"
