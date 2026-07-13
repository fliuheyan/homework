# Data Quality Report

- Generated at: **2026-07-13 01:03:11 UTC**
- Batch: **20260712235140_50f52084**

## Summary by Table

| table | checks | total_issues |
|---|---:|---:|
| raw.customer_raw | 34 | 34 |
| raw.orders_raw | 102 | 102 |
| raw.survey_raw | 34 | 3 |

## raw.customer_raw

| column | description | invalid_count |
|---|---|---:|
| birthday_text | Invalid birthday format | 34 |

## raw.orders_raw

| column | description | invalid_count |
|---|---|---:|
| order_date_text | Invalid order date format | 102 |

## raw.survey_raw

| column | description | invalid_count |
|---|---|---:|
| diet_pref_text | Nutrition is NULL/blank | 3 |
