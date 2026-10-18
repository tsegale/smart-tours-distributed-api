from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Connects to the PostgreSQL container created in Phase 2
SQLALCHEMY_DATABASE_URL = "postgresql://admin:secretpassword@localhost:5432/tours_db"

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()