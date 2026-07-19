"""Domain errors. Each carries an HTTP status and a stable machine-readable code."""


class WalletError(Exception):
    """Base class for expected, client-facing errors."""

    status_code = 400
    code = "wallet_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidRequestError(WalletError):
    status_code = 400
    code = "invalid_request"


class WalletNotFoundError(WalletError):
    status_code = 404
    code = "wallet_not_found"


class IdempotencyConflictError(WalletError):
    """Same idempotency key reused with a different request body."""

    status_code = 409
    code = "idempotency_conflict"


class InsufficientBalanceError(WalletError):
    status_code = 422
    code = "insufficient_balance"


class LockAcquisitionError(WalletError):
    status_code = 503
    code = "wallet_busy"
