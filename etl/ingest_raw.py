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
    clean_first_section = _strip_excel_literals(raw_fmt.split(";")[0])

    if isinstance(v, datetime):
        if ("年" in clean_fmt) and ("月" in clean_fmt) and ("日" in clean_fmt):
            return f"{v.year}年{v.month}月{v.day}日"
        has_date_tokens = (
            any(t in clean_fmt for t in ["y", "d", "年", "月", "日"])
            or bool(re.search(r"[ymd]+[/-][ymd]+", clean_fmt))
        )
        has_time_tokens = (
            any(t in clean_fmt for t in ["h", "s", "时", "分", "秒"])
            or bool(re.search(r"h+:[m]+", clean_fmt))
        )
        if has_time_tokens and not has_date_tokens:
            return v.strftime("%H:%M:%S")
        if has_date_tokens and has_time_tokens:
            return v.strftime("%Y-%m-%d %H:%M:%S")
        if has_date_tokens:
            return f"{v.year:04d}-{v.month:02d}-{v.day:02d}"
        return str(v)

    if isinstance(v, (int, float, Decimal)):
        symbol = next((s for s in CURRENCY_SYMBOLS if s in clean_fmt), None)
        if symbol:
            section = clean_first_section
            decimals = 0
            if "." in section:
                tail = section.split(".", 1)[1]
                for ch in tail:
                    if ch in ("0", "#"):
                        decimals += 1
                    else:
                        break
            number = f"{float(v):.{decimals}f}"
            placeholder_positions = []
            for ch in ("#", "0"):
                pos = section.find(ch)
                if pos != -1:
                    placeholder_positions.append(pos)
            if not placeholder_positions:
                return str(v)
            first_placeholder = min(placeholder_positions)
            symbol_pos = section.find(symbol)
            has_space_near_symbol = (
                (symbol_pos + 1 < len(section) and section[symbol_pos + 1] == " ")
                or (symbol_pos > 0 and section[symbol_pos - 1] == " ")
            )
            space = " " if has_space_near_symbol else ""
            if symbol_pos < first_placeholder:
                return f"{symbol}{space}{number}"
            return f"{number}{space}{symbol}"
        return str(v)

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
