# Data Quality Report

- Generated at: **2026-07-13 01:03:11 UTC**

## Summary by Table

| table | checks | total_issues |
|---|---:|---:|
| raw.customer | 34 | 34 |
| raw.orders | 102 | 102 |
| raw.survey | 34 | 3 |

## raw.customer

| column | description | invalid_count |
|---|---|---:|
| birthday_text | Invalid birthday format | 34 |

## raw.orders

| column | description | invalid_count |
|---|---|---:|
| order_date_text | Invalid order date format | 102 |

## raw.survey

| column | description | invalid_count |
|---|---|---:|
| diet_pref_text | Nutrition is NULL/blank | 3 |
