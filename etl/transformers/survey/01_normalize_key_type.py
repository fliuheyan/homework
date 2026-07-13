import re

def transform(df):
    df["respondent_key"] = df["respondent_key_text"].astype(str).str.strip()

    def f(x):
        if re.match(r"^ORD\d+$", x, re.I): return "order_number"
        if re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", x): return "email"
        return "invalid"

    df["key_type"] = df["respondent_key"].map(f)
    return df