import re
import pandas as pd
from dateutil import parser

def _parse(v):
    if pd.isna(v): return None
    s = str(v).strip().replace("年","-").replace("月","-").replace("日","")
    if re.match(r"^\d{1,2}\s*/\s*[A-Za-z]{3,9}\s*/?$", s):
        s = s.replace("/", " ").strip() + " 2021"
    try:
        return parser.parse(s, dayfirst=False, yearfirst=True).date()
    except:
        return None

def transform(df):
    df["order_date"] = df["order_date_text"].map(_parse)
    return df