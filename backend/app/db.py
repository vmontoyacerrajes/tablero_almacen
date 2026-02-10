from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os

DB_URI = os.getenv("DB_URI", "mysql+pymysql://user:pass@host:3306/moving")
engine = create_engine(DB_URI, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
