import pandas as pd
from dateutil import parser

def _parse(v):
    if pd.isna(v): return None
    s = str(v).strip().replace("年","-").replace("月","-").replace("日","")
    try:
        return parser.parse(s, dayfirst=False, yearfirst=True).date()
    except:
        return None

def transform(df):
    df["birthday"] = df["birthday_text"].map(_parse)
    return df