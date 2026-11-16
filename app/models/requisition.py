"""
SQLAlchemy models for the G-Cortex Requisition Management Subagent.
Handles spatial allocations (venues and athletic facilities), bookings with zero-trust
presence tracking, multi-tier society approval chains, and lab equipment barcode custody.
"""
from sqlalchemy import Column, String, Integer, Float, Boolean, ForeignKey, DateTime, Date, Text
from sqlalchemy.orm import relationship
from app.database import Base


class Venue(Base):
    """Represents a campus venue, laboratory, or athletic space.
    Maps to the 'venues' table.
    """
    __tablename__ = "venues"

    venue_id = Column(String(20), primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    capacity = Column(Integer, nullable=True)
    requires_approval = Column(Boolean, default=False)
    category = Column(String(50), nullable=True)  # 'auditorium', 'lab', 'sports', 'seminar_hall', 'conference_room', 'outdoor'
    facilities = Column(Text, nullable=True)       # Comma-separated list of amenities (e.g. stage, projector)

    # Relationships
    bookings = relationship("Booking", back_populates="venue")
    requisitions = relationship("SocietyRequisition", back_populates="venue")


class Booking(Base):
    """Represents a spatial or athletic facility reservation with zero-trust presence verification.
    Maps to the 'bookings' table.
    """
    __tablename__ = "bookings"

    booking_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    venue_id = Column(String(20), ForeignKey("venues.venue_id"), nullable=False, index=True)
    user_id = Column(String(10), ForeignKey("users.user_id"), nullable=False, index=True)
    booking_time = Column(DateTime, nullable=False)
    duration_hours = Column(Float, default=1.0)
    status = Column(String(20), nullable=False, default="pending")  # 'pending', 'approved', 'auto-released', 'checked_in', 'rejected'
    checkin_token = Column(String(20), nullable=True)               # e.g. 'CHK-4821' for zero-trust check-in PIN
    checkin_status = Column(String(20), default="pending_checkin")  # 'pending_checkin', 'checked_in', 'auto-released'

    # Relationships
    venue = relationship("Venue", back_populates="bookings")
    user = relationship("User", backref="bookings")


class SocietyRequisition(Base):
    """Represents a multi-tier digital requisition for a student society event or major campus venue.
    Tracks progression through Tier 1 (Faculty Advisor) and Tier 2 (Directorate of Student Affairs).
    """
    __tablename__ = "society_requisitions"

    requisition_id = Column(String(20), primary_key=True, index=True)  # e.g. 'REQ-2026-001'
    society_name = Column(String(100), nullable=False)
    venue_id = Column(String(20), ForeignKey("venues.venue_id"), nullable=False)
    applicant_id = Column(String(10), ForeignKey("users.user_id"), nullable=False, index=True)
    event_title = Column(String(150), nullable=False)
    booking_date = Column(Date, nullable=False)
    start_time = Column(String(10), nullable=False)
    duration_hours = Column(Float, default=2.0)
    expected_attendees = Column(Integer, nullable=True)
    justification = Column(Text, nullable=True)
    current_tier = Column(Integer, default=1)                           # 1 = Faculty Advisor, 2 = Student Affairs Admin
    status = Column(String(30), default="pending_advisor_approval")    # 'pending_advisor_approval', 'pending_admin_approval', 'approved', 'rejected'
    created_at = Column(DateTime, nullable=True)

    # Relationships
    venue = relationship("Venue", back_populates="requisitions")
    applicant = relationship("User", backref="society_requisitions")


class LabEquipment(Base):
    """Represents laboratory hardware tracked via barcode asset identifiers.
    Maintains custody status, student borrower registration, and return due dates.
    """
    __tablename__ = "lab_equipment"

    barcode = Column(String(30), primary_key=True, index=True)          # e.g. 'BAR-OSC-201'
    name = Column(String(150), nullable=False)
    lab = Column(String(100), nullable=False)                           # e.g. 'Digital Systems Lab'
    category = Column(String(50), nullable=True)                        # e.g. 'Instrumentation', 'Development Board'
    status = Column(String(30), default="available")                    # 'available', 'in_custody', 'under_repair'
    custodian_id = Column(String(10), ForeignKey("users.user_id"), nullable=True, index=True)
    checkout_date = Column(Date, nullable=True)
    due_date = Column(Date, nullable=True)
    condition = Column(String(50), default="good")                      # 'good', 'fair', 'damaged'
    purpose = Column(String(255), nullable=True)

    # Relationship to student borrower
    custodian = relationship("User", backref="equipment_in_custody")
