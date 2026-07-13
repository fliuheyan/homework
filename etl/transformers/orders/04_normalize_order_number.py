import re

def transform(df):
    df["order_number"] = df["order_number_text"].astype(str).str.strip()
    df.loc[~df["order_number"].str.match(r"^ORD\d+$", na=False), "order_number"] = None
    return df