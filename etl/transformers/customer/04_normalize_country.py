def transform(df):
    def f(v):
        s = str(v).strip().lower()
        if s in ["de", "deutschland", "germany"]:
            return "DE"
        return s.upper()[:2] if s else None
    df["country_code"] = df["country_text"].map(f)
    return df