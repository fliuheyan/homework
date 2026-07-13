# Data Quality Report

- Generated at: **2026-07-13 09:16:02 UTC**

## Summary by Table

| table | total_records | total_issues |
|---|---:|---:|
| raw.customer | 34 | 33 |
| raw.orders | 102 | 90 |
| raw.survey | 34 | 11 |

## raw.customer

| column | description | invalid_count |
|---|---|---:|
| birthday_text | Invalid birthday format (expected YYYY-MM-DD) | 28 |
| gender_text | Invalid gender format (expected male/female) | 10 |
| country_text | Invalid country format (expected DE) | 17 |
| zip_code_text | Invalid zip code format (NULL/blank or not 4-5 digits) | 7 |
| city_text | Invalid city format (NULL/blank or contains zip code) | 10 |

## raw.orders

| column | description | invalid_count |
|---|---|---:|
| order_date_text | Invalid order date format (expected YYYY/M/D or YYYY-MM-DD) | 84 |
| net_amount_text | Invalid net amount format (plain number required, no currency symbols) | 34 |

## raw.survey

| column | description | invalid_count |
|---|---|---:|
| respondent_key_text | Email respondent key matches non-unique customer email | 8 |
| diet_pref_text | Nutrition is NULL/blank | 3 |
