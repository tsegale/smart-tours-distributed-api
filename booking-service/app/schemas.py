from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from .models import BookingStatus


class AvailabilityCreate(BaseModel):
    tour_id: int = Field(gt=0)
    starts_at: datetime
    total_slots: int = Field(gt=0, le=500)


class AvailabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tour_id: int
    starts_at: datetime
    total_slots: int
    booked_slots: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def slots_remaining(self) -> int:
        return self.total_slots - self.booked_slots


class BookingCreate(BaseModel):
    availability_id: int = Field(gt=0)
    slots: int = Field(default=1, gt=0, le=20)


class BookingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    availability_id: int
    tour_id: int
    starts_at: datetime
    slots: int
    status: BookingStatus
    created_at: datetime
    cancelled_at: datetime | None = None
