def transform(df):
    m = {
        "m":"male", "male":"male",
        "f":"female", "female":"female"
    }
    df["gender"] = df["gender_text"].astype(str).str.strip().str.lower().map(m).fillna("unknown")
    return df