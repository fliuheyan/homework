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

VALID_GENDER = {"male", "female", "m", "f"}
VALID_COUNTRY = {"de", "deutschland", "germany"}


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


def amount_ok(v):
    if is_null_like(v):
        return False
    if isinstance(v, (int, float)) and not pd.isna(v):
        return True
    x = str(v).strip().replace("€", "").replace("$", "").replace(" ", "").replace(",", ".")
    if not re.match(r"^-?\d+(\.\d{1,2})?$", x):
        return False
    try:
        float(x)
        return True
    except Exception:
        return False


def city_ok(v):
    return not is_null_like(v)


def reference_ok(v):
    if is_null_like(v):
        return False
    x = str(v).strip()
    return bool(EMAIL_REGEX.match(x) or ORDER_NO_REGEX.match(x))


def check_orders(df):
    t = "raw.orders_raw"
    rows = []

    c_date = safe_col(df, ["order_date_text", "order_date"])
    c_amt = safe_col(df, ["net_amount_text", "net_amount"])

    if c_date:
        m = (~df[c_date].apply(date_ok)) & (~df[c_date].apply(is_null_like))
        rows.append((t, c_date, "Invalid order date format", int(m.sum())))
    if c_amt:
        m = (~df[c_amt].apply(amount_ok)) & (~df[c_amt].apply(is_null_like))
        rows.append((t, c_amt, "Invalid net amount format", int(m.sum())))

    return rows


def check_customer(df):
    t = "raw.customer_raw"
    rows = []

    c_bday = safe_col(df, ["birthday_text", "birthday"])
    c_gender = safe_col(df, ["gender_text", "gender"])
    c_country = safe_col(df, ["country_text", "country"])
    c_city = safe_col(df, ["city_text", "city"])

    if c_bday:
        m = (~df[c_bday].apply(date_ok)) & (~df[c_bday].apply(is_null_like))
        rows.append((t, c_bday, "Invalid birthday format", int(m.sum())))

    if c_gender:
        s = df[c_gender].astype("string").fillna("").str.strip().str.lower()
        m = (~s.isin(VALID_GENDER)) & (~df[c_gender].apply(is_null_like))
        rows.append((t, c_gender, "Invalid gender format (male/female/m/f)", int(m.sum())))

    if c_country:
        s = df[c_country].astype("string").fillna("").str.strip().str.lower()
        m = (~s.isin(VALID_COUNTRY)) & (~df[c_country].apply(is_null_like))
        rows.append((t, c_country, "Invalid country format (DE/Deutschland/Germany)", int(m.sum())))

    if c_city:
        m = ~df[c_city].apply(city_ok)
        rows.append((t, c_city, "Invalid city format (NULL/blank)", int(m.sum())))

    return rows


def check_survey(df):
    t = "raw.survey_raw"
    rows = []

    c_ref = safe_col(df, ["respondent_key_text", "reference"])
    c_nut = safe_col(df, ["diet_pref_text", "nutrition"])

    if c_ref:
        m = (~df[c_ref].apply(reference_ok)) & (~df[c_ref].apply(is_null_like))
        rows.append((t, c_ref, "Invalid reference format (email or ORD+digits)", int(m.sum())))

    if c_nut:
        m = df[c_nut].apply(is_null_like)
        rows.append((t, c_nut, "Nutrition is NULL/blank", int(m.sum())))

    return rows


def write_report(path, batch_label, checks_map, issue_df):
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
        "| table | checks | total_issues |",
        "|---|---:|---:|",
    ]

    for t in ["raw.customer_raw", "raw.orders_raw", "raw.survey_raw"]:
        checks = int(checks_map.get(t, 0))
        sub = issue_df[issue_df["table"] == t]
        total_issues = int(sub["invalid_count"].sum()) if not sub.empty else 0
        lines.append(f"| {t} | {checks} | {total_issues} |")

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
    issues += check_orders(df_orders)
    issues += check_customer(df_customer)
    issues += check_survey(df_survey)

    issue_df = pd.DataFrame(issues, columns=["table", "column", "description", "invalid_count"])

    checks_map = {
        "raw.orders_raw": len(df_orders),
        "raw.customer_raw": len(df_customer),
        "raw.survey_raw": len(df_survey),
    }

    write_report(output_path, batch_label, checks_map, issue_df)
    print(f"[DQ] markdown report generated: {output_path}")


if __name__ == "__main__":
    main()