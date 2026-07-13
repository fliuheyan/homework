import pandas as pd

from etl.data_check import check_customer, check_orders, check_survey, write_report


def _issue_counts(rows):
    return {(column, description): invalid_count for _, column, description, invalid_count in rows}


def test_check_customer_detects_requested_issues():
    df = pd.DataFrame(
        [
            {
                "birthday_text": "1990-01-01",
                "gender_text": "male",
                "country_text": "DE",
                "zip_code_text": "40239",
                "city_text": "Düsseldorf",
            },
            {
                "birthday_text": "01/01/1990",
                "gender_text": "m",
                "country_text": "Deutschland",
                "zip_code_text": "",
                "city_text": "Leipzig 4103",
            },
        ]
    )

    rows, bad_rows = check_customer(df)
    counts = _issue_counts(rows)

    assert counts[("birthday_text", "Invalid birthday format (expected YYYY-MM-DD)")] == 1
    assert counts[("gender_text", "Invalid gender format (expected male/female)")] == 1
    assert counts[("country_text", "Invalid country format (expected DE)")] == 1
    assert counts[("zip_code_text", "Invalid zip code format (NULL/blank or not 4-5 digits)")] == 1
    assert counts[("city_text", "Invalid city format (NULL/blank or contains zip code)")] == 1
    assert bad_rows == {1}


def test_check_customer_rejects_invalid_calendar_dates():
    df = pd.DataFrame(
        [
            {
                "birthday_text": "2024-02-31",
                "gender_text": "female",
                "country_text": "DE",
                "zip_code_text": "40239",
                "city_text": "Düsseldorf",
            }
        ]
    )

    rows, bad_rows = check_customer(df)
    counts = _issue_counts(rows)

    assert counts[("birthday_text", "Invalid birthday format (expected YYYY-MM-DD)")] == 1
    assert bad_rows == {0}


def test_check_orders_detects_non_canonical_date_and_currency_amount():
    df = pd.DataFrame(
        [
            {"order_date_text": "2021-08-01", "net_amount_text": "10.50"},
            {"order_date_text": "08-01-21", "net_amount_text": "10.50 €"},
        ]
    )

    rows, bad_rows = check_orders(df)
    counts = _issue_counts(rows)

    assert counts[("order_date_text", "Invalid order date format (expected YYYY/M/D or YYYY-MM-DD)")] == 1
    assert counts[("net_amount_text", "Invalid net amount format (plain number required, no currency symbols)")] == 1
    assert bad_rows == {1}


def test_check_survey_flags_mixed_reference_types():
    df = pd.DataFrame(
        [
            {"respondent_key_text": "ORD126", "diet_pref_text": "vegan"},
            {"respondent_key_text": "user@example.com", "diet_pref_text": "vegetarian"},
            {"respondent_key_text": "bad-key", "diet_pref_text": None},
        ]
    )

    rows, bad_rows = check_survey(df)
    counts = _issue_counts(rows)

    assert counts[("respondent_key_text", "Invalid reference format (email or ORD+digits)")] == 1
    assert counts[("respondent_key_text", "Mixed reference types detected (email and order number)")] == 2
    assert counts[("diet_pref_text", "Nutrition is NULL/blank")] == 1
    assert bad_rows == {0, 1, 2}


def test_write_report_uses_total_records_and_distinct_bad_rows(tmp_path):
    issue_df = pd.DataFrame(
        [
            ("raw.orders_raw", "order_date_text", "Invalid order date format (expected YYYY/M/D or YYYY-MM-DD)", 1),
            ("raw.orders_raw", "net_amount_text", "Invalid net amount format (plain number required, no currency symbols)", 1),
        ],
        columns=["table", "column", "description", "invalid_count"],
    )

    path = tmp_path / "report.md"
    write_report(
        str(path),
        "BATCH-1",
        {
            "raw.customer_raw": 0,
            "raw.orders_raw": 2,
            "raw.survey_raw": 0,
        },
        issue_df,
        {
            "raw.customer_raw": set(),
            "raw.orders_raw": {0},
            "raw.survey_raw": set(),
        },
    )

    content = path.read_text(encoding="utf-8")
    assert "| table | total_records | total_issues |" in content
    assert "| raw.orders_raw | 2 | 1 |" in content
