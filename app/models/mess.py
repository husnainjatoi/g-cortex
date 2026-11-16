"""
SQLAlchemy models for the G-Cortex Mess Management Subagent.
Handles dining hall meal tracking, gate-pass synchronized rebates,
and meal session ledgers.
"""
from sqlalchemy import Column, String, Integer, Date, Float, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class MessLedger(Base):
    """Represents a student meal consumption or rebate record in the dining hall ledger.
    Maps to the central 'mess_ledger' table.
    """
    __tablename__ = "mess_ledger"

    entry_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id = Column(String(10), ForeignKey("users.user_id"), nullable=False, index=True)
    meal_date = Column(Date, nullable=False)
    meal_type = Column(String(20), nullable=False)  # 'breakfast', 'lunch', 'dinner'
    status = Column(String(20), nullable=False)     # 'consumed', 'rebated', 'no-show'

    # Relationship to user
    user = relationship("User", backref="mess_entries")


class MessRebate(Base):
    """Represents a formal mess rebate claim synchronized with campus security gate-passes.
    Allows students to deduct dining fees when away from campus for >= 2 consecutive days.
    """
    __tablename__ = "mess_rebates"

    rebate_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id = Column(String(10), ForeignKey("users.user_id"), nullable=False, index=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    days_count = Column(Integer, nullable=False)
    rebate_amount_pkr = Column(Float, nullable=False)
    status = Column(String(20), default="pending")  # 'approved', 'rejected', 'pending'
    gate_pass_synced = Column(Boolean, default=False)
    reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=True)

    # Relationship to user
    user = relationship("User", backref="mess_rebates")
