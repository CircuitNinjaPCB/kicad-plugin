from __future__ import annotations

import base64
import hashlib
import secrets
import time
import webbrowser
from collections.abc import Callable
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
from auth_lock import authentication_lock
from credentials import Credentials, CredentialStore, KeyringCredentialStore
from http_transport import ACCEPT_ENCODING
from pydantic import SecretStr, ValidationError
from service_errors import (
    AuthError,
    AuthHTTPError,
    AuthInvalidResponse,
    AuthNetworkError,
    AuthorizationFailed,
    BrowserLaunchFailed,
    CallbackServerError,
    ClientNameConflict,
    ClientRejected,
    ConsentDenied,
    CredentialStorageError,
    DiscoveryError,
    InsufficientScope,
    LoginRequired,
    LoginTimeout,
    Operation,
    ProviderChanged,
    RevocationFailed,
    UntrustedURL,
    network_reason,
    oauth_error_code,
)
from service_models import (
    DEFAULT_CLIENT_NAME,
    AuthorizationRequest,
    AuthorizationServerMetadataResponse,
    ClientRegistrationRequest,
    ClientRegistrationResponse,
    CodeExchangeRequest,
    OAuthEndpoints,
    ProtectedResourceMetadataResponse,
    RefreshTokenRequest,
    TokenResponse,
    validate_client_name,
)
from typing_extensions import Self


def site_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.path not in ("", "/"))
        or (not parsed.hostname)
        or (parsed.scheme not in ("http", "https"))
        or (
            parsed.scheme == "http"
            and parsed.hostname not in ("localhost", "127.0.0.1", "::1")
        )
    ):
        raise ValueError("Expected a secure site origin (or HTTP loopback origin).")
    _ = parsed.port
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def trusted_url(value: str, origin: str, *, operation: Operation | None = None) -> str:
    if "\\" in value or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise UntrustedURL("The server supplied an untrusted URL.", operation=operation)
    try:
        parsed = urlsplit(value)
        base = urlsplit(origin)
        if (
            parsed.scheme != base.scheme
            or parsed.hostname != base.hostname
            or (parsed.port or (443 if parsed.scheme == "https" else 80))
            != (base.port or (443 if base.scheme == "https" else 80))
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise UntrustedURL(
                "The server supplied an untrusted URL.", operation=operation
            )
    except ValueError as exc:
        raise UntrustedURL(
            "The server supplied a malformed URL.", operation=operation
        ) from exc
    return value


class BrowserAuth:
    def __init__(
        self,
        origin: str,
        *,
        store: CredentialStore | None = None,
        client: httpx2.Client | None = None,
        browser_open: Callable[[str], object] = webbrowser.open,
        clock_s: Callable[[], float] = time.time,
        lock_directory: Path | None = None,
        lock_timeout: float = 5,
    ) -> None:
        self.origin = site_origin(origin)
        self.store = store if store is not None else KeyringCredentialStore()
        self._client = (
            client if client is not None else httpx2.Client(timeout=30, trust_env=False)
        )
        self._owns_client = client is None
        self._browser_open = browser_open
        self._clock_s = clock_s
        self._metadata: OAuthEndpoints | None = None
        self._lock_directory = lock_directory
        self._lock_timeout = lock_timeout

    def _lock(self, operation: Operation) -> AbstractContextManager[None]:
        return authentication_lock(
            self.origin,
            operation,
            timeout=self._lock_timeout,
            directory=self._lock_directory,
        )

    def saved_credentials(self) -> Credentials | None:
        with self._lock(Operation.CREDENTIAL_READ):
            return self.store.get(self.origin)

    def _json(
        self,
        method: str,
        url: str,
        *,
        operation: Operation,
        authority: str | None = None,
        empty_ok: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method,
                trusted_url(url, authority or self.origin, operation=operation),
                headers={"Accept-Encoding": ACCEPT_ENCODING},
                follow_redirects=False,
                **kwargs,
            )
        except httpx2.HTTPError as exc:
            reason = network_reason(exc)
            raise AuthNetworkError(
                f"The authentication request failed ({reason.value}).",
                operation=operation,
                reason=reason,
            ) from exc
        if not 200 <= response.status_code < 300:
            code = None
            if len(response.content) <= 65536:
                try:
                    error = response.json()
                except ValueError:
                    error = None
                if isinstance(error, dict):
                    code = oauth_error_code(error.get("error"))
            message = f"Authentication request failed (HTTP {response.status_code})."
            if response.status_code in (400, 401):
                if code == "invalid_client" and operation in (
                    Operation.TOKEN_EXCHANGE,
                    Operation.REFRESH,
                ):
                    raise ClientRejected(
                        "The provider rejected the client registration.",
                        operation=operation,
                        status_code=response.status_code,
                        oauth_error=code,
                    )
                if code == "invalid_grant" and operation == Operation.REFRESH:
                    raise LoginRequired(
                        "The refresh grant expired or was rejected. Sign in again.",
                        operation=operation,
                        status_code=response.status_code,
                        oauth_error=code,
                    )
            raise AuthHTTPError(
                message,
                operation=operation,
                status_code=response.status_code,
                oauth_error=code,
            )
        if empty_ok:
            return {}
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise TypeError("Expected a JSON object.")
            return data
        except (ValueError, TypeError) as exc:
            raise AuthInvalidResponse(
                "The authentication server returned invalid JSON.",
                operation=operation,
                status_code=response.status_code,
            ) from exc

    def discover(self, *, reload: bool = False) -> OAuthEndpoints:
        if reload:
            self._metadata = None
        if self._metadata is None:
            try:
                resource = ProtectedResourceMetadataResponse.model_validate(
                    self._json(
                        "GET",
                        self.origin + "/.well-known/oauth-protected-resource",
                        operation=Operation.DISCOVERY,
                    )
                )
                if not resource.authorization_servers:
                    raise DiscoveryError(
                        "No authorization server was advertised.",
                        operation=Operation.DISCOVERY,
                    )
                issuer = site_origin(resource.authorization_servers[0])
                if self.origin.startswith("https:") and (
                    not issuer.startswith("https:")
                ):
                    raise DiscoveryError(
                        "An HTTPS service cannot use an insecure issuer.",
                        operation=Operation.DISCOVERY,
                    )
                metadata = AuthorizationServerMetadataResponse.model_validate(
                    self._json(
                        "GET",
                        issuer + "/.well-known/oauth-authorization-server",
                        authority=issuer,
                        operation=Operation.DISCOVERY,
                    )
                )
                if (
                    resource.resource != self.origin
                    or metadata.issuer != issuer
                    or "drafting" not in resource.scopes_supported
                    or (
                        not {"drafting", "offline_access"}.issubset(
                            metadata.scopes_supported
                        )
                    )
                    or (
                        not {"authorization_code", "refresh_token"}.issubset(
                            metadata.grant_types_supported
                        )
                    )
                    or ("code" not in metadata.response_types_supported)
                    or ("S256" not in metadata.code_challenge_methods_supported)
                    or ("none" not in metadata.token_endpoint_auth_methods_supported)
                ):
                    raise DiscoveryError(
                        "Unsupported authorization server metadata.",
                        operation=Operation.DISCOVERY,
                    )
                self._metadata = OAuthEndpoints(
                    resource=self.origin,
                    issuer=metadata.issuer,
                    **{
                        key: trusted_url(
                            getattr(metadata, key),
                            issuer,
                            operation=Operation.DISCOVERY,
                        )
                        for key in (
                            "authorization_endpoint",
                            "token_endpoint",
                            "registration_endpoint",
                            "revocation_endpoint",
                        )
                    },
                )
            except ValidationError as exc:
                details = "; ".join(
                    f"{'.'.join(map(str, error['loc']))}: {error['type']}"
                    for error in exc.errors(
                        include_url=False, include_context=False, include_input=False
                    )[:5]
                )
                raise DiscoveryError(
                    f"Invalid authorization server metadata ({details}).",
                    operation=Operation.DISCOVERY,
                ) from exc
            except ValueError as exc:
                raise DiscoveryError(
                    "Invalid authorization server metadata.",
                    operation=Operation.DISCOVERY,
                ) from exc
        return self._metadata

    def _save_tokens(
        self, data: dict[str, Any], credentials: Credentials, *, operation: Operation
    ) -> str:
        try:
            response = TokenResponse.model_validate(data)
        except ValidationError as exc:
            raise AuthInvalidResponse(
                "Invalid token response.", operation=operation
            ) from exc
        (token, refresh, lifetime) = (
            response.access_token,
            response.refresh_token,
            response.expires_in,
        )
        if response.token_type.lower() != "bearer":
            raise AuthInvalidResponse("Invalid token response.", operation=operation)
        if response.scope is not None and "drafting" not in response.scope.split():
            raise InsufficientScope(
                "The authorization grant lacks drafting access.", operation=operation
            )
        credentials.access_token = SecretStr(token)
        credentials.refresh_token = SecretStr(refresh) if refresh else None
        credentials.expires_at = self._clock_s() + lifetime
        self.store.set(self.origin, credentials)
        return token

    def _credentials(
        self, metadata: OAuthEndpoints, *, operation: Operation
    ) -> Credentials | None:
        credentials = self.store.get(self.origin)
        if credentials is not None and credentials.issuer != metadata.issuer:
            self.store.delete(self.origin)
            if operation != Operation.AUTHORIZATION:
                raise ProviderChanged(
                    "The saved provider identity changed or was unknown. Sign in again.",
                    operation=operation,
                )
            return None
        return credentials

    def login(self, timeout: float = 120, *, client_name: str | None = None) -> None:
        with self._lock(Operation.AUTHORIZATION):
            self._login_locked(timeout, client_name=client_name)

    def _login_locked(self, timeout: float, *, client_name: str | None) -> None:
        if timeout <= 0:
            raise ValueError("Sign-in timeout must be positive.")
        if client_name is not None:
            validate_client_name(client_name)
        previous = self.store.get(self.origin)
        metadata = self.discover(reload=True)
        credentials = self._credentials(metadata, operation=Operation.AUTHORIZATION)
        name = client_name or (
            previous.client_name if previous else DEFAULT_CLIENT_NAME
        )
        reusable = credentials is not None and (not credentials.client_rejected)
        if reusable and credentials is not None and (name != credentials.client_name):
            raise ClientNameConflict(
                "Forget this client before registering it with a different name.",
                operation=Operation.AUTHORIZATION,
            )
        deadline = time.monotonic() + timeout
        for attempt in range(2):
            try:
                self._login_once(metadata, credentials, name, deadline)
                return
            except ClientRejected:
                rejected = self.store.get(self.origin)
                if rejected is not None:
                    rejected = rejected.without_tokens()
                    rejected.client_rejected = True
                    self.store.set(self.origin, rejected)
                if attempt or not reusable:
                    raise
                credentials = None
        raise AssertionError("Unreachable login retry state.")

    def _login_once(
        self,
        metadata: OAuthEndpoints,
        credentials: Credentials | None,
        name: str,
        deadline: float,
    ) -> None:
        if time.monotonic() >= deadline:
            raise LoginTimeout(
                "Sign-in did not complete. Check the browser and try again.",
                operation=Operation.AUTHORIZATION,
            )
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        result: dict[str, str] = {}

        class Callback(BaseHTTPRequestHandler):
            timeout = 1.0

            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                callback_state = query.get("state", [])
                valid = (
                    parsed.path == "/callback"
                    and len(callback_state) == 1
                    and secrets.compare_digest(
                        callback_state[0].encode(), state.encode()
                    )
                )
                if valid and len(query.get("code", [])) == 1 and ("error" not in query):
                    result["code"] = query["code"][0]
                elif valid and "error" in query:
                    result["error"] = (
                        oauth_error_code(query["error"][0])
                        if len(query["error"]) == 1
                        else None
                    ) or "unknown_error"
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(
                    b"You may close this window." if valid else b"Invalid callback."
                )

        try:
            listener = HTTPServer(("127.0.0.1", 0), Callback)
        except OSError as exc:
            raise CallbackServerError(
                "The local sign-in callback server could not start.",
                operation=Operation.AUTHORIZATION,
            ) from exc
        with listener as server:
            server.timeout = 0.2
            redirect = f"http://127.0.0.1:{server.server_port}/callback"
            if credentials is None or credentials.client_rejected:
                registered = self._json(
                    "POST",
                    metadata.registration_endpoint,
                    operation=Operation.REGISTRATION,
                    authority=metadata.issuer,
                    json=ClientRegistrationRequest(
                        redirect_uris=[redirect], client_name=name
                    ).model_dump(mode="json"),
                )
                try:
                    client_id = ClientRegistrationResponse.model_validate(
                        registered
                    ).client_id
                    if not client_id:
                        raise ValueError()
                except ValueError as exc:
                    raise AuthInvalidResponse(
                        "Invalid client registration response.",
                        operation=Operation.REGISTRATION,
                    ) from exc
                credentials = Credentials(
                    client_id=client_id, issuer=metadata.issuer, client_name=name
                )
                self.store.set(self.origin, credentials)
            url = (
                metadata.authorization_endpoint
                + ("&" if "?" in metadata.authorization_endpoint else "?")
                + urlencode(
                    AuthorizationRequest(
                        client_id=credentials.client_id,
                        redirect_uri=redirect,
                        state=state,
                        code_challenge=challenge,
                        resource=self.origin,
                    ).model_dump(mode="json")
                )
            )
            try:
                opened = self._browser_open(url)
            except (OSError, webbrowser.Error) as exc:
                raise BrowserLaunchFailed(
                    "The sign-in browser could not be opened.",
                    operation=Operation.AUTHORIZATION,
                ) from exc
            if opened is False:
                raise BrowserLaunchFailed(
                    "The sign-in browser could not be opened.",
                    operation=Operation.AUTHORIZATION,
                )
            while not result and time.monotonic() < deadline:
                try:
                    server.handle_request()
                except OSError as exc:
                    raise CallbackServerError(
                        "The local sign-in callback server failed.",
                        operation=Operation.AUTHORIZATION,
                    ) from exc
            code = result.get("error")
            if code == "invalid_client":
                raise ClientRejected(
                    "The provider rejected the client registration.",
                    operation=Operation.AUTHORIZATION,
                    oauth_error=code,
                )
            if code == "access_denied":
                raise ConsentDenied(
                    "Authorization was declined.",
                    operation=Operation.AUTHORIZATION,
                    oauth_error=code,
                )
            if code:
                raise AuthorizationFailed(
                    "The provider could not complete browser authorization.",
                    operation=Operation.AUTHORIZATION,
                    oauth_error=code,
                )
            if "code" not in result:
                raise LoginTimeout(
                    "Sign-in did not complete. Check the browser and try again.",
                    operation=Operation.AUTHORIZATION,
                )
            data = self._json(
                "POST",
                metadata.token_endpoint,
                operation=Operation.TOKEN_EXCHANGE,
                authority=metadata.issuer,
                data=CodeExchangeRequest(
                    client_id=credentials.client_id,
                    code=result["code"],
                    redirect_uri=redirect,
                    code_verifier=verifier,
                    resource=self.origin,
                ).model_dump(mode="json"),
            )
            self._save_tokens(data, credentials, operation=Operation.TOKEN_EXCHANGE)

    def access_token(self) -> str:
        with self._lock(Operation.REFRESH):
            return self._access_token_locked()

    def _access_token_locked(self) -> str:
        if self.store.get(self.origin) is None:
            raise LoginRequired("Sign in to continue.", operation=Operation.REFRESH)
        saved = self.store.get(self.origin)
        if saved is not None and (
            saved.access_token is None or saved.expires_at <= self._clock_s() + 30
        ):
            return self._refresh_locked()
        credentials = self._credentials(self.discover(), operation=Operation.REFRESH)
        if (
            credentials
            and credentials.access_token
            and (credentials.expires_at > self._clock_s() + 30)
        ):
            return credentials.access_token.get_secret_value()
        return self._refresh_locked()

    def refresh(self) -> str:
        before = self.store.get(self.origin)
        with self._lock(Operation.REFRESH):
            current = self.store.get(self.origin)
            if current != before and current and current.access_token:
                return self._access_token_locked()
            return self._refresh_locked()

    def _refresh_locked(self) -> str:
        if self.store.get(self.origin) is None:
            raise LoginRequired("Sign in to continue.", operation=Operation.REFRESH)
        metadata = self.discover(reload=True)
        credentials = self._credentials(metadata, operation=Operation.REFRESH)
        if (
            credentials is None
            or credentials.client_rejected
            or credentials.refresh_token is None
        ):
            raise LoginRequired("Sign in to continue.", operation=Operation.REFRESH)
        cleared = credentials.without_tokens()
        self.store.set(self.origin, cleared)
        try:
            data = self._json(
                "POST",
                metadata.token_endpoint,
                operation=Operation.REFRESH,
                authority=metadata.issuer,
                data=RefreshTokenRequest(
                    client_id=credentials.client_id,
                    refresh_token=credentials.refresh_token.get_secret_value(),
                    resource=self.origin,
                ).model_dump(mode="json"),
            )
        except ClientRejected:
            cleared.client_rejected = True
            self.store.set(self.origin, cleared)
            raise
        return self._save_tokens(data, credentials, operation=Operation.REFRESH)

    def logout(self) -> None:
        with self._lock(Operation.REVOCATION):
            self._sign_out(forget=False)

    def forget_client(self) -> None:
        with self._lock(Operation.REVOCATION):
            self._sign_out(forget=True)

    def _sign_out(self, *, forget: bool) -> None:
        failure: AuthError | None = None
        failed_token_type: str | None = None
        revoked_token_types: list[str] = []
        try:
            credentials = self.store.get(self.origin)
        except CredentialStorageError as exc:
            if not forget:
                raise
            credentials = None
            failure = exc
        if credentials and (credentials.access_token or credentials.refresh_token):
            try:
                metadata = self.discover(reload=True)
                if credentials.issuer != metadata.issuer:
                    raise ProviderChanged(
                        "The provider changed; manage old access on the previous provider's website.",
                        operation=Operation.REVOCATION,
                    )
                for name in ("refresh_token", "access_token"):
                    token = getattr(credentials, name)
                    if token:
                        failed_token_type = name
                        self._json(
                            "POST",
                            metadata.revocation_endpoint,
                            operation=Operation.REVOCATION,
                            authority=metadata.issuer,
                            empty_ok=True,
                            data={
                                "client_id": credentials.client_id,
                                "token": token.get_secret_value(),
                                "token_type_hint": name,
                            },
                        )
                        revoked_token_types.append(name)
                        failed_token_type = None
            except AuthError as exc:
                failure = exc
        try:
            if forget:
                self.store.delete(self.origin)
            elif credentials is not None:
                self.store.set(self.origin, credentials.without_tokens())
        except CredentialStorageError as exc:
            exc.revocation_failure = failure
            raise
        if (
            isinstance(failure, AuthHTTPError)
            and failure.status_code == 400
            and (failure.oauth_error == "unsupported_token_type")
            and (failed_token_type == "access_token")
            and ("refresh_token" in revoked_token_types)
        ):
            return
        if failure is not None:
            raise RevocationFailed(
                failure,
                client_forgotten=forget,
                failed_token_type=failed_token_type,
                revoked_token_types=tuple(revoked_token_types),
            ) from failure

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
