from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import sqlalchemy_url

# Connection string comes from the DATABASE_URL environment variable (see app/config.py).
SQLALCHEMY_DATABASE_URL = sqlalchemy_url()

engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# All ORM models inherit from this Base
Base = declarative_base()
