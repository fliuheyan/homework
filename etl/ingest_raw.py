import os
import glob
import re
import uuid
from datetime import datetime, date

import pandas as pd
from openpyxl import load_workbook

from etl.db import get_engine

# 预编译：按未被 [...] 括住的 ; 拆分 Excel 格式段
_EXCEL_FMT_SPLIT_RE = re.compile(r";(?![^[]*\])")

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

    # 取正数部分（Excel 格式以 ; 分隔：正数;负数;零;文本），跳过括号内的分号
    parts = _EXCEL_FMT_SPLIT_RE.split(number_format)
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
    # Pattern matches Excel number formats like #,##0.00 (optional thousands + optional decimals)
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
    """将 Excel 日期/时间按 number_format 逐 token 解析，原样输出为文本。

    Excel 规则：m/mm 在紧跟小时 token（h/hh）之后（允许中间有分隔符）时表示分钟，
    其他位置一律表示月份。last_was_hour 用于追踪这一状态；只有遇到实际格式
    token（非分隔符）才会将其重置为 False，分隔符字符不改变该状态。
    """
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, date):
        dt = datetime(v.year, v.month, v.day)
    else:
        return str(v)

    if not fmt or fmt in ("General", "@"):
        return dt.strftime("%Y-%m-%d")

    # 取正数/日期部分（Excel 以 ; 分隔多段，取第一段）
    parts = _EXCEL_FMT_SPLIT_RE.split(fmt)
    pos_fmt = parts[0] if parts else fmt

    result = []
    i = 0
    fl = pos_fmt.lower()
    last_was_hour = False

    while i < len(pos_fmt):
        # 引号内的字面文本，原样保留
        if pos_fmt[i] == '"':
            end = pos_fmt.find('"', i + 1)
            if end == -1:
                # 未闭合引号：将剩余内容作为字面量处理
                result.append(pos_fmt[i + 1:])
                break
            result.append(pos_fmt[i + 1:end])
            i = end + 1
            last_was_hour = False

        # 转义字符
        elif pos_fmt[i] == '\\':
            if i + 1 < len(pos_fmt):
                result.append(pos_fmt[i + 1])
                i += 2
            else:
                i += 1
            last_was_hour = False

        # [...] 块（颜色、区域、条件），直接跳过
        elif pos_fmt[i] == '[':
            end = pos_fmt.find(']', i)
            i = end + 1 if end != -1 else len(pos_fmt)

        # _ 和 * 对齐/填充符，跳过本字符及下一个字符
        elif pos_fmt[i] in ('_', '*'):
            i += 2

        # 年份
        elif fl[i:i+4] == 'yyyy':
            result.append(f'{dt.year:04d}')
            i += 4
            last_was_hour = False
        elif fl[i:i+2] == 'yy':
            result.append(f'{dt.year % 100:02d}')
            i += 2
            last_was_hour = False

        # 月份或分钟（紧跟小时 token 时为分钟，分隔符不影响 last_was_hour）
        elif fl[i:i+4] == 'mmmm':
            result.append(dt.strftime('%B'))
            i += 4
            last_was_hour = False
        elif fl[i:i+3] == 'mmm':
            result.append(dt.strftime('%b'))
            i += 3
            last_was_hour = False
        elif fl[i:i+2] == 'mm':
            result.append(f'{dt.minute:02d}' if last_was_hour else f'{dt.month:02d}')
            i += 2
            last_was_hour = False
        elif fl[i] == 'm':
            result.append(str(dt.minute) if last_was_hour else str(dt.month))
            i += 1
            last_was_hour = False

        # 日
        elif fl[i:i+4] == 'dddd':
            result.append(dt.strftime('%A'))
            i += 4
            last_was_hour = False
        elif fl[i:i+3] == 'ddd':
            result.append(dt.strftime('%a'))
            i += 3
            last_was_hour = False
        elif fl[i:i+2] == 'dd':
            result.append(f'{dt.day:02d}')
            i += 2
            last_was_hour = False
        elif fl[i] == 'd':
            result.append(str(dt.day))
            i += 1
            last_was_hour = False

        # 小时（设置 last_was_hour，供后续 m/mm 判断用）
        elif fl[i:i+2] == 'hh':
            result.append(f'{dt.hour:02d}')
            i += 2
            last_was_hour = True
        elif fl[i] == 'h':
            result.append(str(dt.hour))
            i += 1
            last_was_hour = True

        # 秒
        elif fl[i:i+2] == 'ss':
            result.append(f'{dt.second:02d}')
            i += 2
            last_was_hour = False
        elif fl[i] == 's':
            result.append(str(dt.second))
            i += 1
            last_was_hour = False

        # AM/PM 标记
        elif fl[i:i+5] == 'am/pm':
            result.append('AM' if dt.hour < 12 else 'PM')
            i += 5
            last_was_hour = False
        elif fl[i:i+3] == 'a/p':
            result.append('A' if dt.hour < 12 else 'P')
            i += 3
            last_was_hour = False

        # 其他字符（- / . : 空格，中文字符等）原样保留，且不改变 last_was_hour
        else:
            result.append(pos_fmt[i])
            i += 1

    return ''.join(result)


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
