import pandas as pd

from etl.main import attach_customer_ids_to_orders, pick_random_customer_ids_by_email


def test_pick_random_customer_ids_by_email_keeps_one_customer_per_email():
    customer_df = pd.DataFrame(
        [
            {"customer_id": 1, "email_norm": "dup@example.com"},
            {"customer_id": 2, "email_norm": "dup@example.com"},
            {"customer_id": 3, "email_norm": "unique@example.com"},
            {"customer_id": 4, "email_norm": None},
        ]
    )

    result = pick_random_customer_ids_by_email(customer_df)

    assert list(result.columns) == ["customer_id", "email_norm"]
    assert len(result) == 2
    assert set(result["email_norm"]) == {"dup@example.com", "unique@example.com"}
    dup_customer_id = result.loc[result["email_norm"] == "dup@example.com", "customer_id"].iloc[0]
    assert dup_customer_id in {1, 2}


def test_attach_customer_ids_to_orders_uses_selected_customer_id():
    orders_df = pd.DataFrame(
        [
            {"order_number": "ORD-1", "email_norm": "dup@example.com"},
            {"order_number": "ORD-2", "email_norm": "missing@example.com"},
            {"order_number": "ORD-3", "email_norm": "unique@example.com"},
        ]
    )
    customer_df = pd.DataFrame(
        [
            {"customer_id": 10, "email_norm": "dup@example.com"},
            {"customer_id": 11, "email_norm": "dup@example.com"},
            {"customer_id": 12, "email_norm": "unique@example.com"},
        ]
    )

    result = attach_customer_ids_to_orders(orders_df, customer_df)

    dup_customer_id = result.loc[result["order_number"] == "ORD-1", "customer_id"].iloc[0]
    missing_customer_id = result.loc[result["order_number"] == "ORD-2", "customer_id"].iloc[0]
    unique_customer_id = result.loc[result["order_number"] == "ORD-3", "customer_id"].iloc[0]

    assert dup_customer_id in {10, 11}
    assert pd.isna(missing_customer_id)
    assert unique_customer_id == 12
