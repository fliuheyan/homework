import os
import re
import datetime as pydt
from datetime import datetime, UTC

import pandas as pd
from sqlalchemy import text
from etl.db import get_engine

REPORT_PATH_DEFAULT = "/app/reports/data_quality_report.md"
NULL_LIKE = {"", "null", "none", "nan", "na", "n/a", "-"}

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
ORDER_NO_REGEX = re.compile(r"^ORD\d+$", re.IGNORECASE)
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ZIP_CODE_RE = re.compile(r"^\d{4,5}$")
CITY_ZIP_RE = re.compile(r"\b\d{4,5}\b")

VALID_GENDER = {"male", "female"}
VALID_COUNTRY = {"de"}


def is_datetime_like(v):
    return isinstance(v, (pd.Timestamp, pydt.datetime, pydt.date))


def is_null_like(v):
    if is_datetime_like(v):
        return False
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except Exception:
        pass
    return str(v).strip().lower() in NULL_LIKE


def safe_col(df, names):
    for n in names:
        if n in df.columns:
            return n
    return None


_PG_DATE_RE = re.compile(r"^\d{4}([/\-])\d{1,2}\1\d{1,2}$")
_CURRENCY_RE = re.compile(r"[€$£¥₩]")


def date_ok(v):
    if is_datetime_like(v):
        return True
    if is_null_like(v):
        return False
    x = str(v).strip()
    x = x.replace("年", "/").replace("月", "/").replace("日", "")
    x = re.sub(r"\s+", "", x)
    d = pd.to_datetime(x, errors="coerce", dayfirst=False)
    return not pd.isna(d)


def iso_date_ok(v):
    if is_null_like(v):
        return False
    return bool(ISO_DATE_RE.match(str(v).strip()))


def order_date_ok(v):
    """Accepts datetime objects or year-first date strings with consistent separators, e.g. 2021/9/1 or 2021-09-01."""
    if is_datetime_like(v):
        return True
    if is_null_like(v):
        return False
    x = str(v).strip()
    if not _PG_DATE_RE.match(x):
        return False
    d = pd.to_datetime(x, errors="coerce", dayfirst=False)
    return not pd.isna(d)


def amount_ok(v):
    if is_null_like(v):
        return False
    if isinstance(v, (int, float)) and not pd.isna(v):
        return True
    x = str(v).strip()
    # Currency symbols are not allowed; reject anything containing them
    if _CURRENCY_RE.search(x):
        return False
    x = x.replace(" ", "").replace(",", ".")
    if not re.match(r"^-?\d+(\.\d{1,2})?$", x):
        return False
    try:
        float(x)
        return True
    except Exception:
        return False


def city_ok(v):
    if is_null_like(v):
        return False
    return not bool(CITY_ZIP_RE.search(str(v).strip()))


def zip_code_ok(v):
    if is_null_like(v):
        return False
    return bool(ZIP_CODE_RE.match(str(v).strip()))


def reference_kind(v):
    if is_null_like(v):
        return "null"
    x = str(v).strip()
    if EMAIL_REGEX.match(x):
        return "email"
    if ORDER_NO_REGEX.match(x):
        return "order_number"
    return "invalid"


def reference_ok(v):
    return reference_kind(v) in {"email", "order_number"}


def collect_issue(rows, row_sets, table_name, column_name, description, mask):
    invalid_count = int(mask.sum())
    rows.append((table_name, column_name, description, invalid_count))
    if invalid_count:
        row_sets.setdefault(table_name, set()).update(mask[mask].index.tolist())


def check_orders(df):
    t = "raw.orders_raw"
    rows = []
    row_sets = {t: set()}

    c_date = safe_col(df, ["order_date_text", "order_date"])
    c_amt = safe_col(df, ["net_amount_text", "net_amount"])

    if c_date:
        m = (~df[c_date].apply(order_date_ok)) & (~df[c_date].apply(is_null_like))
        collect_issue(rows, row_sets, t, c_date, "Invalid order date format (expected YYYY/M/D or YYYY-MM-DD)", m)
    if c_amt:
        m = (~df[c_amt].apply(amount_ok)) & (~df[c_amt].apply(is_null_like))
        collect_issue(rows, row_sets, t, c_amt, "Invalid net amount format (plain number required, no currency symbols)", m)

    return rows, row_sets[t]


def check_customer(df):
    t = "raw.customer_raw"
    rows = []
    row_sets = {t: set()}

    c_bday = safe_col(df, ["birthday_text", "birthday"])
    c_gender = safe_col(df, ["gender_text", "gender"])
    c_country = safe_col(df, ["country_text", "country"])
    c_zip = safe_col(df, ["zip_code_text", "zip_code"])
    c_city = safe_col(df, ["city_text", "city"])

    if c_bday:
        m = (~df[c_bday].apply(iso_date_ok)) & (~df[c_bday].apply(is_null_like))
        collect_issue(rows, row_sets, t, c_bday, "Invalid birthday format (expected YYYY-MM-DD)", m)

    if c_gender:
        s = df[c_gender].astype("string").fillna("").str.strip().str.lower()
        m = (~s.isin(VALID_GENDER)) & (~df[c_gender].apply(is_null_like))
        collect_issue(rows, row_sets, t, c_gender, "Invalid gender format (expected male/female)", m)

    if c_country:
        s = df[c_country].astype("string").fillna("").str.strip().str.lower()
        m = (~s.isin(VALID_COUNTRY)) & (~df[c_country].apply(is_null_like))
        collect_issue(rows, row_sets, t, c_country, "Invalid country format (expected DE)", m)

    if c_zip:
        m = ~df[c_zip].apply(zip_code_ok)
        collect_issue(rows, row_sets, t, c_zip, "Invalid zip code format (NULL/blank or not 4-5 digits)", m)

    if c_city:
        m = ~df[c_city].apply(city_ok)
        collect_issue(rows, row_sets, t, c_city, "Invalid city format (NULL/blank or contains zip code)", m)

    return rows, row_sets[t]


def check_survey(df):
    t = "raw.survey_raw"
    rows = []
    row_sets = {t: set()}

    c_ref = safe_col(df, ["respondent_key_text", "reference"])
    c_nut = safe_col(df, ["diet_pref_text", "nutrition"])

    if c_ref:
        m = (~df[c_ref].apply(reference_ok)) & (~df[c_ref].apply(is_null_like))
        collect_issue(rows, row_sets, t, c_ref, "Invalid reference format (email or ORD+digits)", m)

        kinds = df[c_ref].apply(reference_kind)
        has_email = (kinds == "email").any()
        has_order = (kinds == "order_number").any()
        if has_email and has_order:
            m = kinds.isin({"email", "order_number"})
            collect_issue(rows, row_sets, t, c_ref, "Mixed reference types detected (email and order number)", m)

    if c_nut:
        m = df[c_nut].apply(is_null_like)
        collect_issue(rows, row_sets, t, c_nut, "Nutrition is NULL/blank", m)

    return rows, row_sets[t]


def write_report(path, batch_label, total_records_map, issue_df, bad_rows_map):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "# Data Quality Report",
        "",
        f"- Generated at: **{now}**",
        f"- Batch: **{batch_label}**",
        "",
        "## Summary by Table",
        "",
        "| table | total_records | total_issues |",
        "|---|---:|---:|",
    ]

    for t in ["raw.customer_raw", "raw.orders_raw", "raw.survey_raw"]:
        total_records = int(total_records_map.get(t, 0))
        total_issues = len(bad_rows_map.get(t, set()))
        lines.append(f"| {t} | {total_records} | {total_issues} |")

    lines.append("")

    for t in ["raw.customer_raw", "raw.orders_raw", "raw.survey_raw"]:
        lines += [f"## {t}", "", "| column | description | invalid_count |", "|---|---|---:|"]
        sub = issue_df[(issue_df["table"] == t) & (issue_df["invalid_count"] > 0)]
        if sub.empty:
            lines.append("| - | No issues found | 0 |")
        else:
            for _, r in sub.iterrows():
                lines.append(f"| {r['column']} | {r['description']} | {int(r['invalid_count'])} |")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    engine = get_engine()
    output_path = os.getenv("DQ_REPORT_PATH", REPORT_PATH_DEFAULT)
    only_latest = os.getenv("DQ_ONLY_LATEST_BATCH", "true").lower() == "true"

    with engine.begin() as conn:
        if only_latest:
            batch_id = conn.execute(text("""
                SELECT batch_id
                FROM raw.orders_raw
                ORDER BY ingested_at DESC
                LIMIT 1
            """)).scalar()

            if not batch_id:
                print("[DQ] no data")
                return

            df_orders = pd.read_sql(text("SELECT * FROM raw.orders_raw WHERE batch_id=:b"), conn, params={"b": batch_id})
            df_customer = pd.read_sql(text("SELECT * FROM raw.customer_raw WHERE batch_id=:b"), conn, params={"b": batch_id})
            df_survey = pd.read_sql(text("SELECT * FROM raw.survey_raw WHERE batch_id=:b"), conn, params={"b": batch_id})
            batch_label = str(batch_id)
        else:
            df_orders = pd.read_sql(text("SELECT * FROM raw.orders_raw"), conn)
            df_customer = pd.read_sql(text("SELECT * FROM raw.customer_raw"), conn)
            df_survey = pd.read_sql(text("SELECT * FROM raw.survey_raw"), conn)
            batch_label = "ALL"

    issues = []
    bad_rows_map = {}

    order_issues, order_bad_rows = check_orders(df_orders)
    customer_issues, customer_bad_rows = check_customer(df_customer)
    survey_issues, survey_bad_rows = check_survey(df_survey)

    issues += order_issues
    issues += customer_issues
    issues += survey_issues

    issue_df = pd.DataFrame(issues, columns=["table", "column", "description", "invalid_count"])

    total_records_map = {
        "raw.orders_raw": len(df_orders),
        "raw.customer_raw": len(df_customer),
        "raw.survey_raw": len(df_survey),
    }
    bad_rows_map["raw.orders_raw"] = order_bad_rows
    bad_rows_map["raw.customer_raw"] = customer_bad_rows
    bad_rows_map["raw.survey_raw"] = survey_bad_rows

    write_report(output_path, batch_label, total_records_map, issue_df, bad_rows_map)
    print(f"[DQ] markdown report generated: {output_path}")


if __name__ == "__main__":
    main()