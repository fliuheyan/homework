import datetime as pydt
import os
from datetime import datetime

from sqlalchemy import text

from etl.data_check import build_quality_results, load_batch_frames
from etl.db import get_engine


def persist_quality_metrics(engine, batch_label, total_records_map, issue_df, bad_rows_map):
    created_at = datetime.now(pydt.timezone.utc)
    table_records = [
        {
            "batch_id": batch_label,
            "table_name": table_name,
            "total_records": int(total_records_map.get(table_name, 0)),
            "total_issues": int(len(bad_rows_map.get(table_name, set()))),
            "created_at": created_at,
        }
        for table_name in ["raw.customer", "raw.orders", "raw.survey"]
    ]

    issue_records = [
        {
            "batch_id": batch_label,
            "table_name": row["table"],
            "column_name": row["column"],
            "description": row["description"],
            "invalid_count": int(row["invalid_count"]),
            "created_at": created_at,
        }
        for _, row in issue_df.iterrows()
        if int(row["invalid_count"]) > 0
    ]

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM audit.data_quality_issue_summary WHERE batch_id = :b"), {"b": batch_label})
        conn.execute(
            text("""
                INSERT INTO audit.data_quality_table_summary
                    (batch_id, table_name, total_records, total_issues, created_at)
                VALUES
                    (:batch_id, :table_name, :total_records, :total_issues, :created_at)
                ON CONFLICT (batch_id, table_name) DO UPDATE
                SET total_records = EXCLUDED.total_records,
                    total_issues = EXCLUDED.total_issues,
                    created_at = EXCLUDED.created_at
            """),
            table_records,
        )

        if issue_records:
            conn.execute(
                text("""
                    INSERT INTO audit.data_quality_issue_summary
                        (batch_id, table_name, column_name, description, invalid_count, created_at)
                    VALUES
                        (:batch_id, :table_name, :column_name, :description, :invalid_count, :created_at)
                    ON CONFLICT (batch_id, table_name, column_name, description) DO UPDATE
                    SET invalid_count = EXCLUDED.invalid_count,
                        created_at = EXCLUDED.created_at
                """),
                issue_records,
            )


def main():
    engine = get_engine()
    only_latest = os.getenv("DQ_ONLY_LATEST_BATCH", "true").lower() == "true"

    batch_label, df_orders, df_customer, df_survey = load_batch_frames(engine, only_latest=only_latest)
    if not batch_label:
        print("[DQ-METRICS] no data")
        return

    total_records_map, issue_df, bad_rows_map = build_quality_results(df_orders, df_customer, df_survey)
    persist_quality_metrics(engine, batch_label, total_records_map, issue_df, bad_rows_map)
    print(f"[DQ-METRICS] persisted quality metrics for batch: {batch_label}")


if __name__ == "__main__":
    main()
