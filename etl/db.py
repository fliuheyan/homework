import os
from sqlalchemy import create_engine

def get_engine():
    host = os.getenv("DB_HOST", "postgres")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "bi_db")
    user = os.getenv("DB_USER", "bi_user")
    pwd = os.getenv("DB_PASSWORD", "bi_pass")
    return create_engine(f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{name}")