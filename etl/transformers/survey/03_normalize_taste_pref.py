def transform(df):
    s = df["taste_pref_text"].astype(str).str.strip().str.lower()
    df["taste_pref"] = s.replace({
        "sweeet": "sweet",
        "": None
    })
    return df