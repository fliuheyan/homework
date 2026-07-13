import re
import pandas as pd

def transform(df):
    city_out, zip_out = [], []
    for _, r in df.iterrows():
        city = "" if pd.isna(r.get("city_text")) else str(r.get("city_text")).strip()
        zipc = "" if pd.isna(r.get("zip_code_text")) else str(r.get("zip_code_text")).strip()
        m = re.search(r"(\d{4,5})", city)
        if m and not zipc:
            zipc = m.group(1)
            city = re.sub(r"\d{4,5}", "", city).replace(",", "").strip()
        city_out.append(city if city else None)
        zip_out.append(zipc if zipc else None)
    df["city"] = city_out
    df["zip_code"] = zip_out
    df["loyalty_score"] = pd.to_numeric(df["loyalty_score_text"], errors="coerce").astype("Int64")
    return df