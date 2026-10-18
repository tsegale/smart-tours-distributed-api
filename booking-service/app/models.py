from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from .database import Base
from datetime import datetime

class TourAvailability(Base):
    __tablename__ = "tour_availability"

    id = Column(Integer, primary_key=True, index=True)
    tour_id = Column(Integer, index=True, nullable=False) # References the Java Core Service
    date = Column(DateTime, nullable=False)
    total_slots = Column(Integer, nullable=False)
    booked_slots = Column(Integer, default=0)

class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True, nullable=False) # From the JWT token
    tour_availability_id = Column(Integer, ForeignKey("tour_availability.id"))
    status = Column(String, default="CONFIRMED") # CONFIRMED, CANCELLED
    created_at = Column(DateTime, default=datetime.utcnow)