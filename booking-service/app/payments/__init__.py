from functools import lru_cache

from ..config import get_settings
from .base import PaymentProvider


@lru_cache
def get_payment_provider() -> PaymentProvider:
    """FastAPI dependency, also used by the sweeper. Selected by PAYMENT_PROVIDER."""
    settings = get_settings()
    if settings.payment_provider == "stripe":
        from .stripe_provider import StripeProvider

        return StripeProvider(settings)
    from .fake_provider import FakePaymentProvider

    return FakePaymentProvider(settings)
