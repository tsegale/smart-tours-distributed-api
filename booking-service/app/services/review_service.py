"""Verified reviews. A review counts toward a tour's rating only if a real, paid trip stands behind it.

Rules, checked with the booking row locked, so a concurrent cancel can't slip in between:
    - only the booking's owner (anyone else gets the same 404 as a missing booking);
    - only CONFIRMED bookings;
    - only once the departure has started;
    - one review per booking (a unique constraint backs this up).

The review and its review.submitted event commit together through the outbox. tour-core adds the
rating to the tour's average when it consumes the event.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import NotFoundError, ReviewAlreadySubmittedError, ReviewNotAllowedError
from ..models import Booking, BookingStatus, Review
from ..schemas import ReviewCreate
from . import outbox


def submit_review(
    db: Session, user_id: str, booking_id: int, data: ReviewCreate, now: datetime | None = None
) -> Review:
    now = now or datetime.now(UTC)
    try:
        booking = db.execute(
            select(Booking)
            .where(Booking.id == booking_id)
            .with_for_update(of=Booking)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if booking is None or booking.user_id != user_id:
            raise NotFoundError(f"Booking {booking_id} does not exist", booking_id=booking_id)
        if booking.status != BookingStatus.CONFIRMED:
            raise ReviewNotAllowedError(
                f"Only confirmed bookings can be reviewed; this one is {booking.status}", booking_status=booking.status
            )
        if booking.availability.starts_at > now:
            raise ReviewNotAllowedError(
                "Reviews open once the departure has started", starts_at=booking.availability.starts_at.isoformat()
            )
        if db.execute(select(Review.id).where(Review.booking_id == booking_id)).first() is not None:
            raise ReviewAlreadySubmittedError("This booking has already been reviewed", booking_id=booking_id)

        review = Review(
            booking_id=booking_id,
            user_id=user_id,
            tour_id=booking.availability.tour_id,
            rating=data.rating,
            comment=data.comment,
        )
        db.add(review)
        db.flush()
        outbox.record(
            db,
            aggregate_type="review",
            aggregate_id=review.id,
            event_type=outbox.REVIEW_SUBMITTED,
            data={"reviewId": review.id, "bookingId": booking_id, "tourId": review.tour_id, "rating": review.rating},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Two submissions raced past the check; the unique constraint let exactly one through.
        raise ReviewAlreadySubmittedError("This booking has already been reviewed", booking_id=booking_id) from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(review)
    return review
