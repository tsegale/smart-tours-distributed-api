from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import AvailabilityCreate, AvailabilityResponse
from ..security import CurrentUser, get_current_user, require_roles
from ..services import booking_service

router = APIRouter(prefix="/api/availability", tags=["availability"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AvailabilityResponse)
def create_availability(
    body: AvailabilityCreate,
    db: DbSession,
    _operator: Annotated[CurrentUser, Depends(require_roles("OPERATOR", "ADMIN"))],
) -> AvailabilityResponse:
    return AvailabilityResponse.model_validate(booking_service.create_availability(db, body))


@router.get("", response_model=list[AvailabilityResponse])
def list_availability(
    db: DbSession,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    tour_id: Annotated[int, Query(gt=0)],
) -> list[AvailabilityResponse]:
    return [AvailabilityResponse.model_validate(a) for a in booking_service.list_availability(db, tour_id)]
