from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Protocol

import keyring
from keyring.backend import KeyringBackend
from pydantic import BaseModel, SecretStr, ValidationError
from service_errors import CredentialDataError, CredentialStorageError, Operation
from service_models import DEFAULT_CLIENT_NAME, ClientName


class Credentials(BaseModel):
    client_id: str
    issuer: str | None = None
    client_name: ClientName = DEFAULT_CLIENT_NAME
    client_rejected: bool = False
    access_token: SecretStr | None = None
    refresh_token: SecretStr | None = None
    expires_at: float = 0

    def without_tokens(self) -> Credentials:
        return self.model_copy(
            update={"access_token": None, "refresh_token": None, "expires_at": 0}
        )


class CredentialStore(Protocol):
    def get(self, origin: str) -> Credentials | None: ...

    def set(self, origin: str, credentials: Credentials) -> None: ...

    def delete(self, origin: str) -> None: ...


class MemoryCredentialStore:
    def __init__(self) -> None:
        self._entries: dict[str, Credentials] = {}

    def get(self, origin: str) -> Credentials | None:
        value = self._entries.get(origin)
        return value.model_copy(deep=True) if value else None

    def set(self, origin: str, credentials: Credentials) -> None:
        self._entries[origin] = credentials.model_copy(deep=True)

    def delete(self, origin: str) -> None:
        self._entries.pop(origin, None)


@contextmanager
def _keyring_operation(operation: Operation) -> Generator[None, None, None]:
    try:
        yield
    except CredentialStorageError:
        raise
    except Exception as exc:
        raise CredentialStorageError(
            "The system credential store could not complete the requested operation.",
            operation=operation,
        ) from exc


class KeyringCredentialStore:
    def __init__(self, backend: KeyringBackend | None = None) -> None:
        with _keyring_operation(Operation.CREDENTIAL_INIT):
            self._backend = backend if backend is not None else keyring.get_keyring()

    def get(self, origin: str) -> Credentials | None:
        with _keyring_operation(Operation.CREDENTIAL_READ):
            raw = self._backend.get_password("CircuitNinja:" + origin, "oauth")
        try:
            return Credentials.model_validate_json(raw) if raw is not None else None
        except ValidationError as exc:
            raise CredentialDataError(
                "The saved credential record is invalid.",
                operation=Operation.CREDENTIAL_READ,
            ) from exc

    def set(self, origin: str, credentials: Credentials) -> None:
        import json

        data = credentials.model_dump()
        for name in ("access_token", "refresh_token"):
            token = data[name]
            data[name] = token.get_secret_value() if token else None
        with _keyring_operation(Operation.CREDENTIAL_WRITE):
            self._backend.set_password(
                "CircuitNinja:" + origin, "oauth", json.dumps(data)
            )

    def delete(self, origin: str) -> None:
        with _keyring_operation(Operation.CREDENTIAL_DELETE):
            if (
                self._backend.get_password("CircuitNinja:" + origin, "oauth")
                is not None
            ):
                self._backend.delete_password("CircuitNinja:" + origin, "oauth")
