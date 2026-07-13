import pandas as pd
from sqlalchemy import text
from time import perf_counter

from etl.db import get_engine
from etl.plugin_engine import discover_plugins_for_table, run_plugins


def record_step_duration_if_not_set(step_durations, key, started_at):
    if started_at is not None and step_durations[key] is None:
        step_durations[key] = round(perf_counter() - started_at, 4)


def refresh_customer_monthly_order_summary(conn, batch_id):
    """Refresh monthly order totals for one ETL batch inside the active transaction."""
    try:
        conn.execute(
            text("DELETE FROM audit.customer_monthly_order_summary WHERE batch_id = :b"),
            {"b": batch_id},
        )
        conn.execute(text("""
            INSERT INTO audit.customer_monthly_order_summary (
                batch_id,
                customer_id,
                customer_email,
                order_month,
                order_count,
                total_net_amount
            )
            SELECT
                :b AS batch_id,
                o.customer_id,
                c.email AS customer_email,
                DATE_TRUNC('month', o.order_date)::DATE AS order_month,
                COUNT(*)::INT AS order_count,
                SUM(o.net_amount)::NUMERIC(14,2) AS total_net_amount
            FROM core.orders o
            JOIN core.customer c
              ON c.customer_id = o.customer_id
            GROUP BY
                o.customer_id,
                c.email,
                DATE_TRUNC('month', o.order_date)::DATE
        """), {"b": batch_id})
    except Exception as exc:
        raise RuntimeError(
            f"Failed to refresh audit.customer_monthly_order_summary for batch {batch_id}"
        ) from exc


def main():
    engine = get_engine()
    step_durations = {
        "extract_duration_seconds": None,
        "transform_duration_seconds": None,
        "load_duration_seconds": None,
    }
    extract_started = None
    transform_started = None
    load_started = None

    # 1) 找到最新 batch_id（以 orders 为基准；如需更严谨可做三表交集校验）
    with engine.begin() as conn:
        batch_id = conn.execute(text("""
            SELECT batch_id
            FROM raw.orders
            ORDER BY ingested_at DESC
            LIMIT 1
        """)).scalar()

        if not batch_id:
            print("No batch found in raw tables. Please run ingest_raw.py first.")
            return

        run_id = conn.execute(text("""
            INSERT INTO audit.etl_run_log(batch_id, started_at, status)
            VALUES (:b, now(), 'running')
            RETURNING run_id
        """), {"b": batch_id}).scalar()

    try:
        # 2) 读取 raw（仅本次 batch）
        extract_started = perf_counter()
        with engine.begin() as conn:
            df_orders = pd.read_sql(
                text("SELECT * FROM raw.orders WHERE batch_id = :b"),
                conn, params={"b": batch_id}
            )
            df_customer = pd.read_sql(
                text("SELECT * FROM raw.customer WHERE batch_id = :b"),
                conn, params={"b": batch_id}
            )
            df_survey = pd.read_sql(
                text("SELECT * FROM raw.survey WHERE batch_id = :b"),
                conn, params={"b": batch_id}
            )
        record_step_duration_if_not_set(step_durations, "extract_duration_seconds", extract_started)

        # 3) 自动发现插件并执行（按文件名字典序）
        transform_started = perf_counter()
        orders_plugins = discover_plugins_for_table("orders")
        customer_plugins = discover_plugins_for_table("customer")
        survey_plugins = discover_plugins_for_table("survey")

        print("orders plugins:", orders_plugins)
        print("customer plugins:", customer_plugins)
        print("survey plugins:", survey_plugins)

        df_orders = run_plugins(df_orders, orders_plugins)
        df_customer = run_plugins(df_customer, customer_plugins)
        df_survey = run_plugins(df_survey, survey_plugins)

        customer_required_cols = [
            "email", "email_norm", "birthday", "gender",
            "country_code", "zip_code", "city", "loyalty_score"
        ]
        missing_customer_cols = [c for c in customer_required_cols if c not in df_customer.columns]
        if missing_customer_cols:
            raise ValueError(f"customer plugins output missing columns: {missing_customer_cols}")

        orders_required_cols = ["order_number", "email_norm", "order_date", "net_amount"]
        missing_orders_cols = [c for c in orders_required_cols if c not in df_orders.columns]
        if missing_orders_cols:
            raise ValueError(f"orders plugins output missing columns: {missing_orders_cols}")

        survey_required_cols = ["respondent_key", "key_type", "diet_pref", "taste_pref"]
        missing_survey_cols = [c for c in survey_required_cols if c not in df_survey.columns]
        if missing_survey_cols:
            raise ValueError(f"survey plugins output missing columns: {missing_survey_cols}")
        record_step_duration_if_not_set(step_durations, "transform_duration_seconds", transform_started)

        load_started = perf_counter()
        with engine.begin() as conn:
            # 4) Rebuild core tables in one transaction
            conn.execute(text("TRUNCATE core.survey, core.orders, core.customer RESTART IDENTITY CASCADE"))

            customer_insert = df_customer[customer_required_cols].copy()
            customer_insert.to_sql("customer", conn, schema="core", if_exists="append", index=False)

            customer_map = pd.read_sql(text("""
                SELECT customer_id, email_norm
                FROM core.customer
                WHERE email_norm IS NOT NULL
                ORDER BY customer_id DESC
            """), conn)
            customer_map = customer_map.drop_duplicates(subset=["email_norm"], keep="first")

            df_orders_core = df_orders.merge(customer_map, on="email_norm", how="left")
            df_orders_core = df_orders_core.dropna(subset=["customer_id", "order_date", "net_amount", "order_number"])
            df_orders_core = df_orders_core.drop_duplicates(subset=["order_number"], keep="first")

            orders_insert = df_orders_core[["order_number", "customer_id", "order_date", "net_amount"]].copy()
            orders_insert["customer_id"] = orders_insert["customer_id"].astype(int)
            orders_insert.to_sql("orders", conn, schema="core", if_exists="append", index=False)

            order_map = pd.read_sql(text("SELECT order_id, order_number FROM core.orders"), conn)
            customer_map2 = pd.read_sql(text("""
                SELECT customer_id, email_norm
                FROM core.customer
                WHERE email_norm IS NOT NULL
                ORDER BY customer_id DESC
            """), conn)
            unique_email_mask = ~customer_map2["email_norm"].duplicated(keep=False)
            customer_map2 = customer_map2[unique_email_mask]

            df_survey_core = df_survey.copy()
            df_survey_core["order_number_norm"] = df_survey_core["respondent_key"].where(df_survey_core["key_type"] == "order_number")
            df_survey_core["email_norm"] = (
                df_survey_core["respondent_key"]
                .astype(str)
                .str.strip()
                .str.lower()
                .where(df_survey_core["key_type"] == "email")
            )

            df_survey_core = df_survey_core.merge(order_map, left_on="order_number_norm", right_on="order_number", how="left")
            df_survey_core = df_survey_core.merge(customer_map2, on="email_norm", how="left")

            survey_insert = df_survey_core[["respondent_key", "key_type", "customer_id", "order_id", "diet_pref", "taste_pref"]].copy()
            survey_insert.to_sql("survey", conn, schema="core", if_exists="append", index=False)

            # 5) Refresh monthly customer order summary for this batch
            refresh_customer_monthly_order_summary(conn, batch_id)

            # 6) Write back run log
            stats = {
                "ror": conn.execute(text("SELECT COUNT(*) FROM raw.orders WHERE batch_id = :b"), {"b": batch_id}).scalar(),
                "rcr": conn.execute(text("SELECT COUNT(*) FROM raw.customer WHERE batch_id = :b"), {"b": batch_id}).scalar(),
                "rsr": conn.execute(text("SELECT COUNT(*) FROM raw.survey WHERE batch_id = :b"), {"b": batch_id}).scalar(),
                "roc": conn.execute(text("SELECT COUNT(*) FROM core.orders")).scalar(),
                "rcc": conn.execute(text("SELECT COUNT(*) FROM core.customer")).scalar(),
                "rsc": conn.execute(text("SELECT COUNT(*) FROM core.survey")).scalar(),
            }
            record_step_duration_if_not_set(step_durations, "load_duration_seconds", load_started)

            conn.execute(text("""
                UPDATE audit.etl_run_log
                SET ended_at = now(),
                    status = 'success',
                    rows_orders = :ror,
                    rows_customer = :rcr,
                    rows_survey = :rsr,
                    rows_orders_core = :roc,
                    rows_customer_core = :rcc,
                    rows_survey_core = :rsc,
                    extract_duration_seconds = :extract_duration_seconds,
                    transform_duration_seconds = :transform_duration_seconds,
                    load_duration_seconds = :load_duration_seconds
                WHERE run_id = :rid
            """), {**stats, **step_durations, "rid": run_id})

        print(f"Pipeline finished successfully. batch_id={batch_id}")

    except Exception as e:
        record_step_duration_if_not_set(step_durations, "extract_duration_seconds", extract_started)
        record_step_duration_if_not_set(step_durations, "transform_duration_seconds", transform_started)
        record_step_duration_if_not_set(step_durations, "load_duration_seconds", load_started)
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE audit.etl_run_log
                SET ended_at = now(),
                    status = 'failed',
                    error_message = :err,
                    extract_duration_seconds = :extract_duration_seconds,
                    transform_duration_seconds = :transform_duration_seconds,
                    load_duration_seconds = :load_duration_seconds
                WHERE run_id = :rid
            """), {"err": str(e), **step_durations, "rid": run_id})
        raise


if __name__ == "__main__":
    main()