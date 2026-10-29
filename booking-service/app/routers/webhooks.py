"""Public payment-provider webhooks. The gateway forwards these without authentication or
identity headers; the provider's signature over the raw body is the only credential.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..database import SessionLocal, get_db
from ..payments import get_payment_provider
from ..payments.base import PaymentProvider
from ..services import booking_service, webhook_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _issue_refunds(booking_ids: list[int], provider: PaymentProvider) -> None:
    for booking_id in booking_ids:
        with SessionLocal() as db:
            booking_service.issue_refund(db, booking_id, provider)


@router.post(
    "/stripe",
    responses={400: {"description": "Bad or missing signature, or not an event"}},
)
async def stripe_webhook(
    request: Request,
    background: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    provider: Annotated[PaymentProvider, Depends(get_payment_provider)],
    stripe_signature: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    # The exact bytes received: the signature covers them, so the body is never parsed first.
    raw_body = await request.body()
    event = provider.parse_webhook(raw_body, stripe_signature)
    # Database work is blocking; keep it off the event loop.
    result = await run_in_threadpool(webhook_service.handle_event, db, event, provider)
    if result.refunds_due:
        # Answer the provider fast; the refund is already committed as REFUND_PENDING, so if this
        # task dies the sweeper picks it up.
        background.add_task(_issue_refunds, result.refunds_due, provider)
    return {"received": True, "duplicate": result.duplicate}
