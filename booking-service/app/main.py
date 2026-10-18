from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from . import models, database

# Create the database tables
models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="Smart Tours - Booking Service")

class BookingRequest(BaseModel):
    tour_availability_id: int
    user_id: str # In Phase 3, this would be extracted from the validated JWT
    slots_requested: int = 1

@app.post("/api/bookings", status_code=status.HTTP_201_CREATED)
def create_booking(request: BookingRequest, db: Session = Depends(database.get_db)):
    
    # 1. Start a transaction manually
    try:
        # PESSIMISTIC LOCKING: with_for_update() adds "FOR UPDATE" to the SQL query.
        # If User A and User B request this at the same time, User B's database query 
        # will pause and wait until User A's transaction commits or rolls back.
        availability = db.query(models.TourAvailability)\
            .filter(models.TourAvailability.id == request.tour_availability_id)\
            .with_for_update()\
            .first()

        if not availability:
            raise HTTPException(status_code=404, detail="Tour availability record not found.")

        # 2. Check if enough slots exist
        slots_remaining = availability.total_slots - availability.booked_slots
        if request.slots_requested > slots_remaining:
            raise HTTPException(status_code=400, detail="Not enough slots available.")

        # 3. Update the slots
        availability.booked_slots += request.slots_requested

        # 4. Create the booking record
        new_booking = models.Booking(
            user_id=request.user_id,
            tour_availability_id=availability.id
        )
        db.add(new_booking)
        
        # 5. Commit the transaction (Releases the lock for User B)
        db.commit()
        db.refresh(new_booking)

        # Note: In Phase 7, we will publish a RabbitMQ event here: "Booking_Success"

        return {"status": "success", "booking_id": new_booking.id}

    except HTTPException:
        # If it's our own 400 or 404, rollback and re-raise
        db.rollback()
        raise
    except Exception as e:
        # If the database crashes or deadlocks, rollback everything
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error during booking.")