import os
import glob
import re
import uuid
from datetime import datetime, date

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


def _format_numeric_cell(value, number_format: str) -> str:
    """将数值按 Excel number_format 格式化为文本，保留货币符号等字面内容。"""
    if not number_format or number_format in ("General", "@"):
        return str(value)

    # 取正数部分（Excel 格式以 ; 分隔：正数;负数;零;文本）
    parts = re.split(r";(?![^[]*\])", number_format)
    pos_fmt = parts[0] if parts else number_format

    # 提取引号内的字面文本，如 "€" 或 "USD"
    quoted_literals = re.findall(r'"([^"]*)"', pos_fmt)
    # 提取 [$X-locale] 格式的货币符号，如 [$€-407]
    locale_currency = re.findall(r"\[\$([^\-\]]*)", pos_fmt)

    currency_tokens = locale_currency + quoted_literals
    currency_text = " ".join(t for t in currency_tokens if t.strip())

    # 去掉格式操作符，提取数字模式
    stripped = re.sub(r'"[^"]*"', " ", pos_fmt)    # 去除引号字面量
    stripped = re.sub(r"\[.*?\]", "", stripped)     # 去除 [...] 块
    stripped = re.sub(r"[_*].", "", stripped)       # 去除 _X 和 *X（对齐/填充符）
    stripped = re.sub(r"\\(.)", r"\1", stripped)    # 反转义 \x -> x

    # 从数字模式中获取千位分隔符和小数位数
    decimal_places = 0
    use_thousands = False
    m = re.search(r"[#0]+(,[#0]+)*(\.([#0]+))?", stripped)
    if m:
        use_thousands = "," in m.group(0)
        if m.group(3):
            decimal_places = len(m.group(3))

    if use_thousands:
        formatted_num = f"{value:,.{decimal_places}f}"
    else:
        formatted_num = f"{value:.{decimal_places}f}"

    if not currency_text:
        return formatted_num

    # 判断货币符号在数字前还是后
    num_start = re.search(r"[#0]", pos_fmt)
    cur_match = re.search(r'"[^"]*"|\[\$[^\]]*\]', pos_fmt)
    if cur_match and num_start and cur_match.start() < num_start.start():
        return f"{currency_text}{formatted_num}"
    else:
        return f"{formatted_num} {currency_text}"


def _excel_date_to_text(v, fmt: str) -> str:
    """将 Excel 日期/时间按 number_format 输出为文本（覆盖常见格式）。"""
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, date):
        dt = datetime(v.year, v.month, v.day)
    else:
        return str(v)

    f = (fmt or "").lower()

    # 常见纯日期格式
    if "yyyy" in f and "mm" in f and "dd" in f and "h" not in f:
        sep = "/"
        if "-" in f:
            sep = "-"
        elif "." in f:
            sep = "."

        # 兼容 m/d 与 mm/dd
        month = str(dt.month) if "m/" in f or "/m" in f else f"{dt.month:02d}"
        day = str(dt.day) if "d/" in f or "/d" in f else f"{dt.day:02d}"
        year = f"{dt.year:04d}"
        return f"{year}{sep}{month}{sep}{day}"

    # 带时间
    if "h" in f:
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    # 默认日期
    return dt.strftime("%Y-%m-%d")


def _cell_to_display_text(cell):
    """核心：把单元格转换为“显示文本语义”。"""
    v = cell.value
    if v is None:
        return None

    # 日期/时间：按 number_format 输出
    if isinstance(v, (datetime, date)):
        return _excel_date_to_text(v, cell.number_format)

    # 数值：按 number_format 输出（保留货币符号等格式信息）
    if isinstance(v, (int, float)):
        return _format_numeric_cell(v, cell.number_format)

    # 其他类型直接字符串化
    s = str(v)
    return s if s != "" else None


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

            # 关键修复：按单元格值 + number_format 生成文本，避免自动类型规范化
            df = _sheet_to_text_df(file_path, sheet, expected_cols)

            df = add_audit_cols(df, source_file, sheet, batch_id)
            df.to_sql(table, engine, schema=schema, if_exists="append", index=False)
            print(f"Loaded {len(df)} rows -> {schema}.{table} from {source_file}:{sheet}")


if __name__ == "__main__":
    main()
