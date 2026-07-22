"""OpenAPI 3.0 spec for the Wallet Service, served at /openapi.json."""

from typing import Any

_ERROR = {
    "type": "object",
    "properties": {
        "error": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
            },
        }
    },
}

_TRANSACTION = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "format": "uuid"},
        "wallet_id": {"type": "string", "format": "uuid"},
        "type": {"type": "string", "enum": ["topup", "deduct", "refund"]},
        "amount_paise": {"type": "integer"},
        "balance_after_paise": {"type": "integer"},
        "reference_id": {"type": "string", "nullable": True},
        "original_transaction_id": {"type": "string", "format": "uuid", "nullable": True},
        "reason": {"type": "string", "nullable": True},
        "created_at": {"type": "string", "format": "date-time"},
    },
}

_WALLET = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "format": "uuid"},
        "customer_id": {"type": "string", "nullable": True},
        "balance_paise": {"type": "integer"},
        "balance_rupees": {"type": "string"},
    },
}


def _json_body(schema: dict[str, Any]) -> dict[str, Any]:
    return {"required": True, "content": {"application/json": {"schema": schema}}}


def _json_response(description: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"description": description, "content": {"application/json": {"schema": schema}}}


def _id_param() -> dict[str, Any]:
    return {
        "name": "id",
        "in": "path",
        "required": True,
        "schema": {"type": "string", "format": "uuid"},
    }


def _idempotency_header() -> dict[str, Any]:
    return {
        "name": "Idempotency-Key",
        "in": "header",
        "required": False,
        "schema": {"type": "string"},
        "description": "Retry-safe key. Same key returns the original response.",
    }


OPENAPI_SPEC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {
        "title": "Wallet Service",
        "version": "1.0.0",
        "description": "Owns wallet balances, records every money movement, and enforces "
        "the balance constraint. All amounts are integer paise (100 paise = ₹1).",
    },
    "paths": {
        "/wallets": {
            "post": {
                "summary": "Create a wallet",
                "requestBody": _json_body(
                    {
                        "type": "object",
                        "properties": {
                            "customer_id": {"type": "string", "nullable": True},
                            "initial_balance_paise": {"type": "integer", "minimum": 0},
                        },
                    }
                ),
                "responses": {"201": _json_response("Wallet created", _WALLET)},
            }
        },
        "/wallets/{id}/topup": {
            "post": {
                "summary": "Add funds",
                "parameters": [_id_param(), _idempotency_header()],
                "requestBody": _json_body(
                    {
                        "type": "object",
                        "required": ["amount_paise"],
                        "properties": {
                            "amount_paise": {"type": "integer", "minimum": 1},
                            "reference_id": {"type": "string", "nullable": True},
                            "idempotency_key": {"type": "string", "nullable": True},
                        },
                    }
                ),
                "responses": {
                    "201": _json_response("Topup applied", _TRANSACTION),
                    "200": _json_response("Idempotent replay", _TRANSACTION),
                    "404": _json_response("Wallet not found", _ERROR),
                },
            }
        },
        "/wallets/{id}/deduct": {
            "post": {
                "summary": "Deduct funds (idempotent)",
                "parameters": [_id_param(), _idempotency_header()],
                "requestBody": _json_body(
                    {
                        "type": "object",
                        "required": ["amount_paise"],
                        "properties": {
                            "amount_paise": {"type": "integer", "minimum": 1},
                            "reference_id": {"type": "string", "nullable": True},
                            "idempotency_key": {"type": "string", "nullable": True},
                        },
                    }
                ),
                "responses": {
                    "201": _json_response("Deduction applied", _TRANSACTION),
                    "200": _json_response("Idempotent replay", _TRANSACTION),
                    "409": _json_response("Idempotency key reused with a different body", _ERROR),
                    "422": _json_response("Insufficient balance", _ERROR),
                    "404": _json_response("Wallet not found", _ERROR),
                },
            }
        },
        "/wallets/{id}/refund": {
            "post": {
                "summary": "Refund a topup or deduct (idempotent)",
                "description": "Reverses exactly one earlier topup or deduct. Refunding a "
                "deduct credits the wallet; refunding a topup debits it. Refunds are "
                "full-amount only and each transaction can be refunded at most once.",
                "parameters": [_id_param(), _idempotency_header()],
                "requestBody": _json_body(
                    {
                        "type": "object",
                        "required": ["original_transaction_id"],
                        "properties": {
                            "original_transaction_id": {"type": "string", "format": "uuid"},
                            "idempotency_key": {"type": "string", "nullable": True},
                            "reason": {"type": "string", "nullable": True, "maxLength": 500},
                        },
                    }
                ),
                "responses": {
                    "201": _json_response("Refund applied", _TRANSACTION),
                    "200": _json_response("Idempotent replay", _TRANSACTION),
                    "400": _json_response("Missing idempotency key or invalid body", _ERROR),
                    "404": _json_response("Wallet or original transaction not found", _ERROR),
                    "409": _json_response(
                        "Idempotency key reused with a different body, or already refunded",
                        _ERROR,
                    ),
                    "422": _json_response(
                        "Transaction not refundable, or refund would overdraw the wallet",
                        _ERROR,
                    ),
                },
            }
        },
        "/wallets/{id}/balance": {
            "get": {
                "summary": "Get balance",
                "parameters": [_id_param()],
                "responses": {
                    "200": _json_response(
                        "Current balance",
                        {
                            "type": "object",
                            "properties": {
                                "wallet_id": {"type": "string"},
                                "balance_paise": {"type": "integer"},
                                "balance_rupees": {"type": "string"},
                            },
                        },
                    ),
                    "404": _json_response("Wallet not found", _ERROR),
                },
            }
        },
        "/wallets/{id}/transactions": {
            "get": {
                "summary": "Get transaction history",
                "parameters": [_id_param()],
                "responses": {
                    "200": _json_response(
                        "Ledger entries, newest first",
                        {
                            "type": "object",
                            "properties": {
                                "transactions": {"type": "array", "items": _TRANSACTION}
                            },
                        },
                    ),
                    "404": _json_response("Wallet not found", _ERROR),
                },
            }
        },
        "/health": {
            "get": {"summary": "Health check", "responses": {"200": {"description": "Healthy"}}}
        },
        "/metrics": {
            "get": {
                "summary": "Prometheus metrics",
                "responses": {"200": {"description": "Metrics"}},
            }
        },
    },
}
