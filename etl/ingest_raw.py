import os
import glob
import uuid
import re
from datetime import datetime
from decimal import Decimal

import pandas as pd
from openpyxl import load_workbook

from etl.db import get_engine

SHEET_TO_RAW_TABLE = {
    "orders": ("raw", "orders_raw"),
    "customer": ("raw", "customer_raw"),
    "survey": ("raw", "survey_raw"),
}

RAW_COLUMNS = {
    "orders": ["order_date_text", "email_text", "net_amount_text", "order_number_text"],
    "customer": [
        "email_text",
        "birthday_text",
        "gender_text",
        "country_text",
        "zip_code_text",
        "city_text",
        "loyalty_score_text",
    ],
    "survey": ["respondent_key_text", "diet_pref_text", "taste_pref_text"],
}

CURRENCY_SYMBOLS = ["€", "$", "£", "¥", "₩"]
MONTH_NAMES_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_NAMES_FULL = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
WEEKDAY_NAMES_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_NAMES_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _strip_excel_literals(fmt: str) -> str:
    out = []
    in_quotes = False
    in_brackets = False
    for ch in fmt:
        if ch == '"':
            in_quotes = not in_quotes
            continue
        if ch == "[" and not in_quotes:
            in_brackets = True
            continue
        if ch == "]" and in_brackets:
            in_brackets = False
            continue
        if in_quotes or in_brackets:
            continue
        out.append(ch)
    return "".join(out)


def _first_format_section(raw_fmt: str) -> str:
    if not raw_fmt:
        return ""
    in_quotes = False
    for i, ch in enumerate(raw_fmt):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ";" and not in_quotes:
            return raw_fmt[:i]
    return raw_fmt


def _iter_format_parts(section: str):
    i = 0
    while i < len(section):
        ch = section[i]
        if ch == '"':
            j = i + 1
            while j < len(section) and section[j] != '"':
                j += 1
            yield ("literal", section[i + 1:j])
            i = j + 1
            continue
        if ch == "\\" and i + 1 < len(section):
            yield ("literal", section[i + 1])
            i += 2
            continue
        if ch in ("_", "*"):
            i += 2
            continue
        if ch == "[":
            j = section.find("]", i + 1)
            i = len(section) if j == -1 else j + 1
            continue
        if ch.isalpha():
            j = i + 1
            while j < len(section) and section[j].lower() == ch.lower():
                j += 1
            yield ("token", section[i:j])
            i = j
            continue
        yield ("literal", ch)
        i += 1


def _clean_number_format_section(section: str) -> str:
    cleaned = []
    for kind, value in _iter_format_parts(section):
        if kind == "token":
            cleaned.append(value)
        else:
            cleaned.append(value)
    return "".join(cleaned)


def _is_minute_token(parts: list[tuple[str, str]], idx: int) -> bool:
    prev_token = next((v.lower() for k, v in reversed(parts[:idx]) if k == "token"), "")
    next_token = next((v.lower() for k, v in parts[idx + 1:] if k == "token"), "")
    return prev_token.startswith("h") or next_token.startswith("s")


def _render_datetime_with_excel_format(v: datetime, section: str) -> str:
    parts = list(_iter_format_parts(section))
    rendered = []
    for idx, (kind, value) in enumerate(parts):
        if kind == "literal":
            rendered.append(value)
            continue

        token = value.lower()
        if token.startswith("y"):
            rendered.append(f"{v.year % 100:02d}" if len(token) == 2 else f"{v.year:04d}")
        elif token.startswith("d"):
            if len(token) == 1:
                rendered.append(str(v.day))
            elif len(token) == 2:
                rendered.append(f"{v.day:02d}")
            elif len(token) == 3:
                rendered.append(WEEKDAY_NAMES_ABBR[v.weekday()])
            else:
                rendered.append(WEEKDAY_NAMES_FULL[v.weekday()])
        elif token.startswith("m"):
            if _is_minute_token(parts, idx):
                rendered.append(f"{v.minute:02d}" if len(token) >= 2 else str(v.minute))
            elif len(token) == 1:
                rendered.append(str(v.month))
            elif len(token) == 2:
                rendered.append(f"{v.month:02d}")
            elif len(token) == 3:
                rendered.append(MONTH_NAMES_ABBR[v.month - 1])
            else:
                rendered.append(MONTH_NAMES_FULL[v.month - 1])
        elif token.startswith("h"):
            rendered.append(f"{v.hour:02d}" if len(token) >= 2 else str(v.hour))
        elif token.startswith("s"):
            rendered.append(f"{v.second:02d}" if len(token) >= 2 else str(v.second))
        else:
            rendered.append(value)
    return "".join(rendered)


def _cell_to_display_text(cell):
    """Return Excel-like display text for raw ingestion."""
    v = cell.value
    if v is None:
        return None
    if isinstance(v, str):
        return v

    raw_fmt = (cell.number_format or "")
    fmt = raw_fmt.lower()
    clean_fmt = _strip_excel_literals(fmt)
    first_section = _first_format_section(raw_fmt)
    clean_first_section = _strip_excel_literals(first_section)

    if isinstance(v, datetime):
        has_date_tokens = any(t in clean_fmt for t in ["y", "d", "年", "月", "日"]) or bool(
            re.search(r"(y+|d+|m{3,4}|m{1,2})", clean_fmt)
        )
        has_time_tokens = any(t in clean_fmt for t in ["h", "s", "时", "分", "秒"]) or bool(
            re.search(r"(h+|s+)", clean_fmt)
        )
        if has_date_tokens or has_time_tokens:
            return _render_datetime_with_excel_format(v, first_section)
        return str(v)

    if isinstance(v, (int, float, Decimal)):
        section = _clean_number_format_section(first_section)
        decimals = 0
        if "." in section:
            tail = section.split(".", 1)[1]
            for ch in tail:
                if ch in ("0", "#"):
                    decimals += 1
                elif ch == ",":
                    continue
                else:
                    break
        hash_pos = section.find("#")
        zero_pos = section.find("0")
        positions = [p for p in (hash_pos, zero_pos) if p != -1]
        if not positions:
            return str(v)
        first_placeholder = min(positions)
        integer_part = section.split(".", 1)[0]
        use_grouping = "," in integer_part
        number = format(float(v), f",.{decimals}f" if use_grouping else f".{decimals}f")
        last_placeholder = max(section.rfind("#"), section.rfind("0"), section.rfind("?"))
        prefix = section[:first_placeholder]
        suffix = section[last_placeholder + 1 :]
        if any(symbol in prefix or symbol in suffix for symbol in CURRENCY_SYMBOLS):
            return f"{prefix}{number}{suffix}".strip()
        return number

    return str(v)


def _sheet_to_text_df(file_path: str, sheet_name: str, expected_cols: list[str]) -> pd.DataFrame:
    """
    使用 openpyxl 读取单元格值 + number_format，生成 raw 所需文本值。
    只读取前 len(expected_cols) 列，跳过首行表头。
    """
    wb = load_workbook(file_path, data_only=False, read_only=True)
    try:
        ws = wb[sheet_name]
        rows = []
        max_col = len(expected_cols)

        for row_idx, row in enumerate(ws.iter_rows(min_col=1, max_col=max_col), start=1):
            if row_idx == 1:
                continue

            vals = [_cell_to_display_text(c) for c in row]

            # 整行为空则跳过
            if all(v is None for v in vals):
                continue

            rows.append(vals)

        return pd.DataFrame(rows, columns=expected_cols)
    finally:
        wb.close()


def add_audit_cols(df, source_file, sheet_name, batch_id):
    df = df.copy()
    df["source_file"] = source_file
    df["sheet_name"] = sheet_name
    df["load_time"] = datetime.utcnow()
    df["batch_id"] = batch_id
    df["row_num_in_sheet"] = range(2, len(df) + 2)  # 假设第1行是表头
    return df


def main():
    engine = get_engine()
    data_dir = os.getenv("DATA_DIR", "/app/data")
    files = glob.glob(os.path.join(data_dir, "*.xlsx")) + glob.glob(os.path.join(data_dir, "*.xls"))
    if not files:
        print("No excel files found in data dir.")
        return

    batch_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + str(uuid.uuid4())[:8]
    print("batch_id:", batch_id)

    for file_path in files:
        source_file = os.path.basename(file_path)

        # 先拿到 sheet 列表
        xls = pd.ExcelFile(file_path)
        for sheet in xls.sheet_names:
            sheet_l = sheet.strip().lower()
            if sheet_l not in SHEET_TO_RAW_TABLE:
                continue

            schema, table = SHEET_TO_RAW_TABLE[sheet_l]
            expected_cols = RAW_COLUMNS[sheet_l]

            # 原样读取 Excel 单元格值，不对字段做额外处理
            df = _sheet_to_text_df(file_path, sheet, expected_cols)

            df = add_audit_cols(df, source_file, sheet, batch_id)
            df.to_sql(table, engine, schema=schema, if_exists="append", index=False)
            print(f"Loaded {len(df)} rows -> {schema}.{table} from {source_file}:{sheet}")


if __name__ == "__main__":
    main()
