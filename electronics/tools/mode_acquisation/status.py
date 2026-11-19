"""Stable, evidence-based result contract for the ADI model acquirer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum


class AcquisitionStatus(StrEnum):
    DOWNLOADED = "downloaded"
    IMPORTED = "imported"
    UNAVAILABLE = "unavailable"
    ACCESS_REQUIRED = "access_required"
    PAID = "paid"
    ERROR = "error"


class ReasonCode(StrEnum):
    PART_NOT_FOUND = "PART_NOT_FOUND"
    MODEL_NOT_PUBLISHED = "MODEL_NOT_PUBLISHED"
    REQUESTED_FORMAT_NOT_AVAILABLE = "REQUESTED_FORMAT_NOT_AVAILABLE"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    LICENSE_ACCEPTANCE_REQUIRED = "LICENSE_ACCEPTANCE_REQUIRED"
    PAID_RESOURCE = "PAID_RESOURCE"
    THIRD_PARTY_PROVIDER_REQUIRED = "THIRD_PARTY_PROVIDER_REQUIRED"
    DOWNLOAD_BLOCKED = "DOWNLOAD_BLOCKED"
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_DOWNLOAD = "INVALID_DOWNLOAD"


@dataclass
class AcquisitionResult:
    status: AcquisitionStatus
    part_number: str
    asset_type: str
    reason_code: ReasonCode | None = None
    reason: str | None = None
    source_url: str | None = None
    download_url: str | None = None
    files: list[str] = field(default_factory=list)
    sha256: dict[str, str] = field(default_factory=dict)
    model_resources: list[dict] = field(default_factory=list)
    exact_part_model_found: bool = False
    series_model_count: int = 0
    resource_errors: list[dict] = field(default_factory=list)
    acquisition_method: str = "https"

    def to_dict(self) -> dict:
        return asdict(self)
