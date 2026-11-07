from sqlalchemy import Column, String
from app.database import Base

class User(Base):
    __tablename__ = "users"
    
    user_id = Column(String(10), primary_key=True, index=True)
    full_name = Column(String(100), nullable=False)
    role = Column(String(20), nullable=False)
    email = Column(String(100), unique=True, nullable=False)