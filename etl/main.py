import pandas as pd
from sqlalchemy import text

from etl.db import get_engine
from etl.plugin_engine import discover_plugins_for_table, run_plugins


CUSTOMER_SELECTION_SEED = 0


def pick_random_customer_ids_by_email(full_customer_df: pd.DataFrame) -> pd.DataFrame:
    customer_map = full_customer_df.dropna(subset=["email_norm"]).copy()
    if customer_map.empty:
        return customer_map

    customer_map = customer_map.sample(frac=1, random_state=CUSTOMER_SELECTION_SEED)
    customer_map = customer_map.drop_duplicates(subset=["email_norm"], keep="first")
    return customer_map[["customer_id", "email_norm"]]


def attach_customer_ids_to_orders(df_orders: pd.DataFrame, full_customer_df: pd.DataFrame) -> pd.DataFrame:
    customer_map = pick_random_customer_ids_by_email(full_customer_df)
    return df_orders.merge(customer_map, on="email_norm", how="left")


def main():
    engine = get_engine()

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

        # 3) 自动发现插件并执行（按文件名字典序）
        orders_plugins = discover_plugins_for_table("orders")
        customer_plugins = discover_plugins_for_table("customer")
        survey_plugins = discover_plugins_for_table("survey")

        print("orders plugins:", orders_plugins)
        print("customer plugins:", customer_plugins)
        print("survey plugins:", survey_plugins)

        df_orders = run_plugins(df_orders, orders_plugins)
        df_customer = run_plugins(df_customer, customer_plugins)
        df_survey = run_plugins(df_survey, survey_plugins)

        # 4) 清空 core（作业场景可接受；生产建议增量 merge）
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE core.survey, core.orders, core.customer RESTART IDENTITY CASCADE"))

        # 5) 写入 core.customer（允许 email 重复）
        customer_required_cols = [
            "email", "email_norm", "birthday", "gender",
            "country_code", "zip_code", "city", "loyalty_score"
        ]
        missing_customer_cols = [c for c in customer_required_cols if c not in df_customer.columns]
        if missing_customer_cols:
            raise ValueError(f"customer plugins output missing columns: {missing_customer_cols}")

        customer_insert = df_customer[customer_required_cols].copy()
        customer_insert.to_sql("customer", engine, schema="core", if_exists="append", index=False)

        # 6) Link orders to customer_id via email_norm (randomly select one customer_id for duplicate emails)
        with engine.begin() as conn:
            customer_map = pd.read_sql(text("""
                SELECT customer_id, email_norm
                FROM core.customer
                WHERE email_norm IS NOT NULL
            """), conn)

        orders_required_cols = ["order_number", "email_norm", "order_date", "net_amount"]
        missing_orders_cols = [c for c in orders_required_cols if c not in df_orders.columns]
        if missing_orders_cols:
            raise ValueError(f"orders plugins output missing columns: {missing_orders_cols}")

        df_orders = attach_customer_ids_to_orders(df_orders, customer_map)
        df_orders = df_orders.dropna(subset=["customer_id", "order_date", "net_amount", "order_number"])
        df_orders = df_orders.drop_duplicates(subset=["order_number"], keep="first")

        orders_insert = df_orders[["order_number", "customer_id", "order_date", "net_amount"]].copy()
        orders_insert["customer_id"] = orders_insert["customer_id"].astype(int)
        orders_insert.to_sql("orders", engine, schema="core", if_exists="append", index=False)

        # 7) survey 关联 order / customer
        with engine.begin() as conn:
            order_map = pd.read_sql(text("SELECT order_id, order_number FROM core.orders"), conn)
            customer_map2 = pd.read_sql(text("""
                SELECT customer_id, email_norm
                FROM core.customer
                WHERE email_norm IS NOT NULL
                ORDER BY customer_id DESC
            """), conn)

        unique_email_mask = ~customer_map2["email_norm"].duplicated(keep=False)
        customer_map2 = customer_map2[unique_email_mask]

        survey_required_cols = ["respondent_key", "key_type", "diet_pref", "taste_pref"]
        missing_survey_cols = [c for c in survey_required_cols if c not in df_survey.columns]
        if missing_survey_cols:
            raise ValueError(f"survey plugins output missing columns: {missing_survey_cols}")

        df_survey["order_number_norm"] = df_survey["respondent_key"].where(df_survey["key_type"] == "order_number")
        df_survey["email_norm"] = (
            df_survey["respondent_key"]
            .astype(str)
            .str.strip()
            .str.lower()
            .where(df_survey["key_type"] == "email")
        )

        df_survey = df_survey.merge(order_map, left_on="order_number_norm", right_on="order_number", how="left")
        df_survey = df_survey.merge(customer_map2, on="email_norm", how="left")

        survey_insert = df_survey[["respondent_key", "key_type", "customer_id", "order_id", "diet_pref", "taste_pref"]].copy()
        survey_insert.to_sql("survey", engine, schema="core", if_exists="append", index=False)

        # 8) 回写 run log
        with engine.begin() as conn:
            stats = {
                "ror": conn.execute(text("SELECT COUNT(*) FROM raw.orders WHERE batch_id = :b"), {"b": batch_id}).scalar(),
                "rcr": conn.execute(text("SELECT COUNT(*) FROM raw.customer WHERE batch_id = :b"), {"b": batch_id}).scalar(),
                "rsr": conn.execute(text("SELECT COUNT(*) FROM raw.survey WHERE batch_id = :b"), {"b": batch_id}).scalar(),
                "roc": conn.execute(text("SELECT COUNT(*) FROM core.orders")).scalar(),
                "rcc": conn.execute(text("SELECT COUNT(*) FROM core.customer")).scalar(),
                "rsc": conn.execute(text("SELECT COUNT(*) FROM core.survey")).scalar(),
            }

            conn.execute(text("""
                UPDATE audit.etl_run_log
                SET ended_at = now(),
                    status = 'success',
                    rows_orders = :ror,
                    rows_customer = :rcr,
                    rows_survey = :rsr,
                    rows_orders_core = :roc,
                    rows_customer_core = :rcc,
                    rows_survey_core = :rsc
                WHERE run_id = :rid
            """), {**stats, "rid": run_id})

        print(f"Pipeline finished successfully. batch_id={batch_id}")

    except Exception as e:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE audit.etl_run_log
                SET ended_at = now(),
                    status = 'failed',
                    error_message = :err
                WHERE run_id = :rid
            """), {"err": str(e), "rid": run_id})
        raise


if __name__ == "__main__":
    main()