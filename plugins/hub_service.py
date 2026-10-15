from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import httpx2
from browser_auth import BrowserAuth, site_origin
from credentials import Credentials
from http_transport import ACCEPT_ENCODING
from service import CustomerService
from service_config import DEFAULT_ORIGIN
from service_errors import (
    AuthenticationBusy,
    AuthLockError,
    CredentialStorageError,
    LoginRequired,
    Operation,
    OrderingNotAllowed,
    PreferencesStorageError,
    RevocationFailed,
)
from service_models import DEFAULT_CLIENT_NAME, BoardStoredResponse, MeResponse


@dataclass(frozen=True)
class AccountState:
    profile: MeResponse | None = None
    client_id: str | None = None
    client_name: str = DEFAULT_CLIENT_NAME
    default_org_id: str | None = None
    notice: str | None = None
    invalid_default_org: bool = False


@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    message: str
    checked_at: datetime


def _settings_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Application Support"
    else:
        configured = os.environ.get("XDG_CONFIG_HOME")
        root = (
            Path(configured)
            if configured and Path(configured).is_absolute()
            else Path.home() / ".config"
        )
    return root / "CircuitNinja" / "kicad-hub.json"


def ordering_problem(profile: MeResponse, organization_id: str | None) -> str:
    if organization_id is None:
        return "" if profile.can_order else "Your profile cannot place personal orders."
    organization = next(
        (org for org in profile.organizations if org.id == organization_id), None
    )
    if organization is None:
        return "The selected organization no longer exists or is not available to this account."
    return "" if organization.can_order else f"{organization.name} cannot place orders."


class HubService:
    def __init__(
        self,
        origin: str = DEFAULT_ORIGIN,
        *,
        auth: BrowserAuth | None = None,
        service: CustomerService | None = None,
        health_client: httpx2.Client | None = None,
        settings_path: Path | None = None,
    ) -> None:
        self.origin = site_origin(origin)
        if service is not None:
            if not isinstance(service.auth, BrowserAuth):
                raise ValueError("The hub requires browser authentication.")
            if auth is not None and service.auth is not auth:
                raise ValueError("The service and hub must share authentication.")
            auth = service.auth
        if auth is not None and auth.origin != self.origin:
            raise ValueError("The authentication origin must match the hub origin.")
        if service is not None and service.origin != self.origin:
            raise ValueError("The service origin must match the hub origin.")
        self._owns_auth = auth is None
        self._auth = auth
        self._owns_service = service is None
        self._service = service
        self._owns_health = health_client is None
        self._health = (
            health_client
            if health_client is not None
            else httpx2.Client(timeout=3, trust_env=False)
        )
        self._settings_path = (
            settings_path if settings_path is not None else _settings_path()
        )
        self._state = AccountState()

    @property
    def _account_auth(self) -> BrowserAuth:
        if self._auth is None:
            self._auth = BrowserAuth(self.origin)
        return self._auth

    @property
    def _account_service(self) -> CustomerService:
        if self._service is None:
            self._service = CustomerService(self.origin, auth=self._account_auth)
        return self._service

    @property
    def state(self) -> AccountState:
        return replace(
            self._state,
            profile=self._state.profile.model_copy(deep=True)
            if self._state.profile is not None
            else None,
        )

    def _guest(self, credentials: Credentials | None = None) -> None:
        self._state = AccountState(
            client_id=credentials.client_id if credentials else None,
            client_name=credentials.client_name if credentials else DEFAULT_CLIENT_NAME,
        )

    def load_account(self) -> AccountState:
        self._guest()
        credentials = self._account_auth.saved_credentials()
        self._guest(credentials)
        if credentials is None or not (
            credentials.access_token or credentials.refresh_token
        ):
            return self.state
        try:
            profile = self._account_service.get_me()
        except Exception:
            try:
                self._guest(self._account_auth.saved_credentials())
            except (CredentialStorageError, AuthenticationBusy, AuthLockError):
                pass
            raise
        self._guest(self._account_auth.saved_credentials())
        self._state = replace(self._state, profile=profile)
        (settings, notice) = self._read_settings()
        remembered = settings.get(self.origin, {}).get(profile.user.id)
        valid = any(
            org.id == remembered and org.can_order for org in profile.organizations
        )
        self._state = replace(self._state, default_org_id=remembered, notice=notice)
        if remembered is not None and (not valid):
            self._state = replace(
                self._state,
                invalid_default_org=True,
                notice="The saved default organization is unavailable or cannot order. Choose an ordering identity before uploading.",
            )
        return self.state

    def login(self, client_name: str | None = None) -> AccountState:
        try:
            self._account_auth.login(client_name=client_name)
        except Exception:
            try:
                self._guest(self._account_auth.saved_credentials())
            except (CredentialStorageError, AuthenticationBusy, AuthLockError):
                pass
            raise
        return self.load_account()

    def _sign_out(self, *, forget: bool) -> AccountState:
        try:
            if forget:
                self._account_auth.forget_client()
            else:
                self._account_auth.logout()
        except (RevocationFailed, CredentialStorageError) as exc:
            if exc.local_tokens_cleared:
                self._state = AccountState(
                    client_id=None if exc.client_forgotten else self._state.client_id,
                    client_name=DEFAULT_CLIENT_NAME
                    if exc.client_forgotten
                    else self._state.client_name,
                    notice=str(exc),
                )
            raise
        return self.load_account()

    def logout(self) -> AccountState:
        return self._sign_out(forget=False)

    def forget_client(self) -> AccountState:
        (settings, notice) = self._read_settings()
        if notice:
            raise PreferencesStorageError(
                "Cannot forget client: saved account preferences could not be read."
            )
        if self.origin in settings:
            del settings[self.origin]
            try:
                self._write_settings(settings)
            except OSError as exc:
                raise PreferencesStorageError(
                    "Cannot forget client: saved account preferences could not be removed."
                ) from exc
        self._state = replace(self._state, default_org_id=None)
        return self._sign_out(forget=True)

    def select_organization(self, organization_id: str | None) -> AccountState:
        profile = self._state.profile
        if organization_id is not None and (
            profile is None
            or not any(
                org.id == organization_id and org.can_order
                for org in profile.organizations
            )
        ):
            raise ValueError(
                "This organization cannot place orders. The saved selection was kept."
            )
        self._save_organization(organization_id)
        return self.state

    def _read_settings(self) -> tuple[dict[str, dict[str, str]], str | None]:
        try:
            raw = json.loads(self._settings_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not all(
                isinstance(origin, str)
                and isinstance(users, dict)
                and all(
                    isinstance(user, str) and isinstance(org, str)
                    for (user, org) in users.items()
                )
                for (origin, users) in raw.items()
            ):
                raise ValueError("Invalid preferences")
            return (raw, None)
        except FileNotFoundError:
            return ({}, None)
        except (OSError, ValueError, UnicodeError):
            return ({}, "Default organization preferences could not be read.")

    def _save_organization(self, organization_id: str | None) -> None:
        self._state = replace(
            self._state,
            default_org_id=organization_id,
            notice=None,
            invalid_default_org=False,
        )
        profile = self._state.profile
        if profile is None:
            return
        (settings, notice) = self._read_settings()
        if notice:
            self._state = replace(self._state, notice=notice)
            return
        users = settings.setdefault(self.origin, {})
        if organization_id is None:
            users.pop(profile.user.id, None)
        else:
            users[profile.user.id] = organization_id
        try:
            self._write_settings(settings)
        except OSError:
            self._state = replace(
                self._state, notice="The default organization could not be saved."
            )

    def _write_settings(self, settings: dict[str, dict[str, str]]) -> None:
        if not settings:
            self._settings_path.unlink(missing_ok=True)
            return
        temporary: Path | None = None
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._settings_path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(settings, stream)
            temporary.replace(self._settings_path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def upload_board(
        self, archive: Path, organization_id: str | None, user_id: str
    ) -> BoardStoredResponse:
        profile = self._account_service.get_me()
        if profile.user.id != user_id:
            raise LoginRequired(
                "The signed-in account changed. Reopen Upload from the hub.",
                operation=Operation.UPLOAD,
            )
        problem = ordering_problem(profile, organization_id)
        if problem:
            raise OrderingNotAllowed(problem, operation=Operation.UPLOAD)
        return self._account_service.create_order(
            archive, organization_id=organization_id
        )

    def check_health(self) -> HealthStatus:
        healthy = False
        try:
            response = self._health.get(
                self.origin + "/api/health",
                headers={"Accept-Encoding": ACCEPT_ENCODING},
                timeout=3,
                follow_redirects=False,
            )
            if response.status_code == 200:
                data = response.json()
                healthy = (
                    isinstance(data, dict)
                    and data.get("status") == "ok"
                    and (data.get("db") == "ok")
                )
                message = (
                    "Service is healthy."
                    if healthy
                    else "Invalid service health response."
                )
            else:
                message = f"Service health check returned HTTP {response.status_code}."
        except httpx2.TimeoutException:
            message = "Service health check timed out."
        except httpx2.HTTPError:
            message = "Service health check could not connect."
        except (ValueError, UnicodeError):
            message = "Invalid service health response."
        return HealthStatus(healthy, message, datetime.now(timezone.utc))

    def close(self) -> None:
        if self._owns_health:
            self._health.close()
        if self._owns_service and self._service is not None:
            self._service.close()
        if self._owns_auth and self._auth is not None:
            self._auth.close()
