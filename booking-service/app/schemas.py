from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from .models import BookingStatus, PaymentStatus


class AvailabilityCreate(BaseModel):
    tour_id: int = Field(gt=0)
    starts_at: datetime
    total_slots: int = Field(gt=0, le=500)


class AvailabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tour_id: int
    tour_name: str
    starts_at: datetime
    unit_price: Decimal
    currency: str
    total_slots: int
    booked_slots: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def slots_remaining(self) -> int:
        return self.total_slots - self.booked_slots


class BookingCreate(BaseModel):
    availability_id: int = Field(gt=0)
    slots: int = Field(default=1, gt=0, le=20)


class PaymentSummary(BaseModel):
    provider: str
    status: PaymentStatus
    amount_minor: int
    currency: str


class CancellationSummary(BaseModel):
    policy: str
    refund_amount: Decimal | None = None
    refund_status: str | None = None
    explanation: str


class BookingResponse(BaseModel):
    id: int
    availability_id: int
    tour_id: int
    starts_at: datetime
    slots: int
    status: BookingStatus
    unit_price: Decimal
    total_amount: Decimal
    currency: str
    hold_expires_at: datetime | None = None
    # Where to send the customer to pay. Present while the booking is PENDING_PAYMENT.
    checkout_url: str | None = None
    payment: PaymentSummary | None = None
    created_at: datetime
    confirmed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation: CancellationSummary | None = None
