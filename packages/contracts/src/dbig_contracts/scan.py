"""Signed, versioned scan requests shared by requester and site collectors."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .artifact import canonical
from .errors import OperatorError


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ir_version: str = "1.0"
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    source_id: str = Field(min_length=1)
    scopes: list[str] = Field(min_length=1)
    capabilities: list[Literal["metadata", "sample", "history"]] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=256)
    requester_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    issued_at: datetime
    expires_at: datetime
    reply_location: str = Field(min_length=1)

    @field_validator("ir_version")
    @classmethod
    def version(cls, value: str) -> str:
        if not value.startswith("1.") or not value[2:].isdecimal():
            raise ValueError("unsupported request major version")
        return value

    @model_validator(mode="after")
    def valid_window(self) -> ScanRequest:
        if (
            self.issued_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.expires_at <= self.issued_at
        ):
            raise ValueError("invalid scan request time window")
        return self


class SignedScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: ScanRequest
    key_id: str
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class ScanReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ir_version: str = "1.0"
    request_id: str
    status: Literal["submitted", "queued", "running", "complete", "failed"]
    received_at: datetime
    bundle_path: str | None = None
    # Why a scan failed: the collector's one-line operator message, or only the exception type
    # for anything else, whose text could carry connection details. Absent unless failed.
    error: str | None = Field(default=None, max_length=1024)


def sign_scan(request: ScanRequest, key_id: str, key: bytes) -> SignedScanRequest:
    if len(key) < 32:
        raise ValueError("signing key too short")
    payload = canonical({"key_id": key_id, "request": request.model_dump(mode="json")})
    signature = hmac.new(key, payload, hashlib.sha256).hexdigest()
    return SignedScanRequest(request=request, key_id=key_id, signature=signature)


def verify_scan(envelope: SignedScanRequest, key: bytes, *, clock_skew_seconds: int = 0) -> None:
    expected = sign_scan(envelope.request, envelope.key_id, key).signature
    if not hmac.compare_digest(expected, envelope.signature):
        raise OperatorError("invalid scan signature")
    now = datetime.now(UTC)
    if envelope.request.expires_at <= now or envelope.request.issued_at > now + timedelta(
        seconds=clock_skew_seconds
    ):
        raise OperatorError("expired or future scan request")
