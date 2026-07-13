def transform(df):
    s = df["diet_pref_text"].astype(str).str.strip().str.lower()
    df["diet_pref"] = s.replace({
        "vegetarisch": "vegetarian",
        "": None
    })
    return df