import os
import glob
import uuid
import pandas as pd
from datetime import datetime
from etl.db import get_engine

SHEET_TO_RAW_TABLE = {
    "orders":   ("raw", "orders_raw"),
    "customer": ("raw", "customer_raw"),
    "survey":   ("raw", "survey_raw"),
}

def _to_raw_text(value):
    """
    raw 层要求：保持 Excel 原始值语义，不做类型清洗/标准化。
    - NaN/None -> None（入库为 NULL）
    - 其他 -> 字符串原样表示
    """
    if pd.isna(value):
        return None
    return str(value)


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
        # 关键修复：按文本读取，避免 pandas 自动把日期/数值改写
        xls = pd.ExcelFile(file_path)
        for sheet in xls.sheet_names:
            sheet_l = sheet.strip().lower()
            if sheet_l not in SHEET_TO_RAW_TABLE:
                continue

            schema, table = SHEET_TO_RAW_TABLE[sheet_l]
            df = pd.read_excel(xls, sheet, dtype=str, keep_default_na=False)

            if sheet_l == "orders":
                df = df.iloc[:, :4]
                df.columns = ["order_date_text", "email_text", "net_amount_text", "order_number_text"]
            elif sheet_l == "customer":
                df = df.iloc[:, :7]
                df.columns = ["email_text", "birthday_text", "gender_text", "country_text", "zip_code_text", "city_text", "loyalty_score_text"]
            elif sheet_l == "survey":
                df = df.iloc[:, :3]
                df.columns = ["respondent_key_text", "diet_pref_text", "taste_pref_text"]

            # 关键修复：raw 文本字段强制字符串透传，禁止隐式格式化
            text_cols = [c for c in df.columns if c.endswith("_text")]
            for col in text_cols:
                df[col] = df[col].map(_to_raw_text)

            df = add_audit_cols(df, source_file, sheet, batch_id)
            df.to_sql(table, engine, schema=schema, if_exists="append", index=False)
            print(f"Loaded {len(df)} rows -> {schema}.{table} from {source_file}:{sheet}")

if __name__ == "__main__":
    main()
