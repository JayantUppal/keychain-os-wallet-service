"""Request validation. All money amounts are integer paise (100 paise = ₹1)."""

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    # Reject unknown fields so typos in the request body fail loudly.
    model_config = ConfigDict(extra="forbid")


class CreateWalletRequest(_StrictModel):
    customer_id: str | None = None
    initial_balance_paise: int = Field(default=0, ge=0)


class TopupRequest(_StrictModel):
    amount_paise: int = Field(gt=0)
    reference_id: str | None = None
    idempotency_key: str | None = None


class DeductRequest(_StrictModel):
    amount_paise: int = Field(gt=0)
    reference_id: str | None = None
    idempotency_key: str | None = None
