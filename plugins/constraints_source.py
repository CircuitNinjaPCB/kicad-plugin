from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx2
from browser_auth import site_origin
from models import ManufacturingConstraints
from pydantic import BaseModel, model_validator
from service import CustomerService
from service_config import DEFAULT_ORIGIN
from service_errors import ServiceError

OFFLINE_PATH = Path(__file__).with_name("manufacturing_constraints.json")


@dataclass(frozen=True)
class ConstraintsResult:
    constraints: ManufacturingConstraints
    warning: str | None
    source: Literal["service", "cache", "offline"]
    fetched_at: datetime | None = None


class _CachedConstraints(BaseModel):
    origin: str
    fetched_at: datetime
    constraints: ManufacturingConstraints

    @model_validator(mode="after")
    def require_timezone(self) -> _CachedConstraints:
        if self.fetched_at.utcoffset() is None:
            raise ValueError("The cache timestamp must include its timezone.")
        self.fetched_at = self.fetched_at.astimezone(timezone.utc)
        return self


class _PublicAuthentication:
    def access_token(self) -> str:
        raise RuntimeError("Manufacturing constraints must be fetched publicly.")

    def refresh(self) -> str:
        raise RuntimeError("Manufacturing constraints must be fetched publicly.")


def _fetch_constraints(origin: str) -> ManufacturingConstraints:
    with (
        httpx2.Client(timeout=8, trust_env=False) as client,
        CustomerService(origin, auth=_PublicAuthentication(), client=client) as service,
    ):
        return service.get_constraints()


def constraints_cache_path(origin: str) -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Caches"
    else:
        configured = os.environ.get("XDG_CACHE_HOME")
        root = (
            Path(configured)
            if configured and Path(configured).is_absolute()
            else Path.home() / ".cache"
        )
    identity = hashlib.sha256(site_origin(origin).encode("utf-8")).hexdigest()
    return root / "circuitninja" / "constraints" / f"{identity}.json"


def _save_cache(path: Path, record: _CachedConstraints) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(record.model_dump_json(indent=2))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_constraints(
    origin: str = DEFAULT_ORIGIN,
    *,
    cache_path: Path | None = None,
    offline_path: Path | None = None,
    fetch: Callable[[str], object] | None = None,
) -> ConstraintsResult:
    origin = site_origin(origin)
    try:
        stored_path = (
            cache_path if cache_path is not None else constraints_cache_path(origin)
        )
    except (OSError, RuntimeError):
        stored_path = None
    try:
        constraints = ManufacturingConstraints.model_validate(
            (fetch or _fetch_constraints)(origin)
        )
    except (ServiceError, OSError, ValueError, TypeError):
        pass
    else:
        fetched_at = datetime.now(timezone.utc)
        if stored_path is not None:
            try:
                _save_cache(
                    stored_path,
                    _CachedConstraints(
                        origin=origin, fetched_at=fetched_at, constraints=constraints
                    ),
                )
            except (OSError, ValueError):
                pass
        return ConstraintsResult(constraints, None, "service", fetched_at)
    if stored_path is not None:
        try:
            record = _CachedConstraints.model_validate_json(
                stored_path.read_text(encoding="utf-8")
            )
            if record.origin != origin:
                raise ValueError("The cached constraints belong to another service.")
        except (OSError, ValueError):
            pass
        else:
            stamp = record.fetched_at.isoformat().replace("+00:00", "Z")
            return ConstraintsResult(
                record.constraints,
                f"Fresh manufacturing constraints could not be obtained from {origin}. Using the last saved values fetched at {stamp}.",
                "cache",
                record.fetched_at,
            )
    bundled = offline_path if offline_path is not None else OFFLINE_PATH
    constraints = ManufacturingConstraints.from_path(bundled)
    return ConstraintsResult(
        constraints,
        f"Fresh manufacturing constraints could not be obtained from {origin}, and no valid cached values are available. Using the offline values from {bundled.resolve()}.",
        "offline",
    )
