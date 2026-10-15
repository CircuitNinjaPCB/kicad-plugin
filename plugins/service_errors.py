from enum import Enum

import httpx2
from service_models import ApiErrorResponse


class Operation(str, Enum):
    DISCOVERY = "discovery"
    REGISTRATION = "registration"
    AUTHORIZATION = "authorization"
    TOKEN_EXCHANGE = "token_exchange"
    REFRESH = "refresh"
    REVOCATION = "revocation"
    CREDENTIAL_INIT = "credential_init"
    CREDENTIAL_READ = "credential_read"
    CREDENTIAL_WRITE = "credential_write"
    CREDENTIAL_DELETE = "credential_delete"
    SERVICE_REQUEST = "service_request"
    SERVICE_RESPONSE = "service_response"
    UPLOAD = "upload"


class ClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        operation: Operation | None = None,
        status_code: int | None = None,
        oauth_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.status_code = status_code
        self.oauth_error = oauth_error


class AuthError(ClientError):
    pass


class AuthenticationBusy(AuthError):
    pass


class AuthLockError(AuthError):
    pass


class PreferencesStorageError(ClientError):
    pass


class ServiceError(ClientError):
    def __init__(
        self,
        message: str,
        *,
        operation: Operation | None = None,
        status_code: int | None = None,
        api_error: ApiErrorResponse | None = None,
        oauth_error: str | None = None,
    ) -> None:
        super().__init__(
            message,
            operation=operation,
            status_code=status_code,
            oauth_error=oauth_error,
        )
        self.api_error = api_error


class LoginRequired(AuthError):
    pass


class ProviderChanged(LoginRequired):
    pass


class LoginTimeout(AuthError):
    pass


class ConsentDenied(AuthError):
    pass


class BrowserLaunchFailed(AuthError):
    pass


class CallbackServerError(AuthError):
    pass


class ClientRejected(AuthError):
    pass


class ClientNameConflict(AuthError):
    pass


class UnsupportedAuthOperation(AuthError):
    pass


class InsufficientScope(AuthError):
    pass


class AuthHTTPError(AuthError):
    pass


class AuthorizationFailed(AuthError):
    pass


class NetworkReason(str, Enum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    PROTOCOL = "protocol"
    TRANSPORT = "transport"


class NetworkError(ClientError):
    def __init__(
        self, message: str, *, operation: Operation, reason: NetworkReason
    ) -> None:
        super().__init__(message, operation=operation)
        self.reason = reason


class AuthNetworkError(NetworkError, AuthError):
    pass


class ServiceNetworkError(NetworkError, ServiceError):
    pass


class InvalidResponse(ClientError):
    pass


class AuthInvalidResponse(InvalidResponse, AuthError):
    pass


class ServiceInvalidResponse(InvalidResponse, ServiceError):
    pass


class DiscoveryError(AuthInvalidResponse):
    pass


class UntrustedURL(AuthInvalidResponse):
    pass


class ServiceHTTPError(ServiceError):
    pass


class OrderingNotAllowed(ServiceError):
    pass


class UploadError(ServiceError):
    pass


class UploadTooLarge(UploadError):
    pass


class CredentialStorageError(AuthError):
    def __init__(self, message: str, *, operation: Operation) -> None:
        super().__init__(message, operation=operation)
        self.local_tokens_cleared = False
        self.client_forgotten = False
        self.revocation_failure: AuthError | None = None


class CredentialDataError(CredentialStorageError):
    pass


class RevocationFailed(AuthError):
    def __init__(
        self,
        failure: AuthError,
        *,
        client_forgotten: bool,
        failed_token_type: str | None = None,
        revoked_token_types: tuple[str, ...] = (),
    ) -> None:
        message = "Server revocation failed; local credentials were cleared."
        if (
            failed_token_type == "access_token"
            and failure.status_code == 400
            and (failure.oauth_error == "unsupported_token_type")
        ):
            message = "Signed out locally. The server cannot revoke this access token; it may remain valid until expiry."
            if "refresh_token" in revoked_token_types:
                message += " The refresh token was revoked."
        elif failed_token_type in ("access_token", "refresh_token"):
            message += f" Failed token: {failed_token_type.removesuffix('_token')}."
        super().__init__(
            message,
            operation=Operation.REVOCATION,
            status_code=failure.status_code,
            oauth_error=failure.oauth_error,
        )
        self.failure = failure
        self.local_tokens_cleared = True
        self.client_forgotten = client_forgotten
        self.failed_token_type = failed_token_type
        self.revoked_token_types = revoked_token_types


def network_reason(error: httpx2.HTTPError) -> NetworkReason:
    if isinstance(error, httpx2.TimeoutException):
        return NetworkReason.TIMEOUT
    if isinstance(error, httpx2.ConnectError):
        return NetworkReason.CONNECTION
    if isinstance(error, httpx2.ProtocolError):
        return NetworkReason.PROTOCOL
    return NetworkReason.TRANSPORT


def oauth_error_code(value: object) -> str | None:
    known = {
        "invalid_request",
        "invalid_client",
        "invalid_grant",
        "unauthorized_client",
        "unsupported_grant_type",
        "unsupported_token_type",
        "invalid_scope",
        "access_denied",
        "server_error",
        "temporarily_unavailable",
        "unsupported_response_type",
        "invalid_target",
        "invalid_client_metadata",
        "invalid_redirect_uri",
        "login_required",
        "consent_required",
        "interaction_required",
    }
    if isinstance(value, str):
        return value if value in known else "unknown_error"
    return None
