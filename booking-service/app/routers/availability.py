from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from ..clients.tour_client import TourClient, get_tour_client
from ..database import get_db
from ..schemas import AvailabilityCreate, AvailabilityResponse
from ..security import CurrentUser, get_current_user, require_roles
from ..services import booking_service

router = APIRouter(prefix="/api/availability", tags=["availability"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=AvailabilityResponse,
    responses={
        404: {"description": "The tour does not exist in tour-core-service"},
        503: {"description": "tour-core-service is unavailable; retryable"},
    },
)
def create_availability(
    body: AvailabilityCreate,
    db: DbSession,
    tours: Annotated[TourClient, Depends(get_tour_client)],
    _operator: Annotated[CurrentUser, Depends(require_roles("OPERATOR", "ADMIN"))],
) -> AvailabilityResponse:
    try:
        return AvailabilityResponse.model_validate(booking_service.create_availability(db, body, tours))
    finally:
        db.rollback()  # end the read transaction before the endpoint returns (see database.get_db)


@router.get("", response_model=list[AvailabilityResponse])
def list_availability(
    db: DbSession,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    tour_id: Annotated[int, Query(gt=0)],
) -> list[AvailabilityResponse]:
    try:
        return [AvailabilityResponse.model_validate(a) for a in booking_service.list_availability(db, tour_id)]
    finally:
        db.rollback()
