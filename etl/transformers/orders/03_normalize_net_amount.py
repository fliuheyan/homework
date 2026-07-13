import pandas as pd

def transform(df):
    s = df["net_amount_text"].astype(str).str.replace("€","", regex=False).str.replace(" ","", regex=False)
    df["net_amount"] = pd.to_numeric(s, errors="coerce")
    return df