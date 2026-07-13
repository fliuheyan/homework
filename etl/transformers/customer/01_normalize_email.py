def transform(df):
    df["email"] = df["email_text"].astype(str).str.strip()
    df["email_norm"] = df["email"].str.lower()
    return df