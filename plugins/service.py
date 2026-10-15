from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, TypeVar
from urllib.parse import quote

import httpx2
from browser_auth import BrowserAuth, site_origin, trusted_url
from http_transport import ACCEPT_ENCODING
from models import ManufacturingConstraints
from pydantic import BaseModel, ValidationError
from service_errors import (
    Operation,
    ServiceError,
    ServiceHTTPError,
    ServiceInvalidResponse,
    ServiceNetworkError,
    UnsupportedAuthOperation,
    UntrustedURL,
    UploadError,
    UploadTooLarge,
    network_reason,
)
from service_models import (
    ApiErrorResponse,
    BoardStoredResponse,
    CreateProjectRequest,
    MeResponse,
    OptionsResponse,
    OrderParameters,
    OrderPriceResponse,
    OrderResponse,
    PriceRequest,
    PriceResponse,
    ProjectCreatedResponse,
    ProjectResponse,
)
from typing_extensions import Self

__all__ = ["Authentication", "CustomerService", "ServiceError"]


class Authentication(Protocol):
    def access_token(self) -> str: ...

    def refresh(self) -> str: ...


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class CustomerService:
    def __init__(
        self,
        origin: str,
        *,
        auth: Authentication | None = None,
        client: httpx2.Client | None = None,
    ) -> None:
        self.origin = site_origin(origin)
        self._client = (
            client if client is not None else httpx2.Client(timeout=60, trust_env=False)
        )
        self._owns_client = client is None
        self.auth = auth if auth is not None else BrowserAuth(self.origin)
        self._owns_auth = auth is None

    def _request(
        self, method: str, path: str, *, authenticated: bool = True, **kwargs: Any
    ) -> httpx2.Response:
        headers = {"Accept-Encoding": ACCEPT_ENCODING}
        if authenticated:
            headers["Authorization"] = "Bearer " + self.auth.access_token()
        url = self.origin + "/api/v1/customer" + path
        try:
            response = self._client.request(
                method, url, headers=headers, follow_redirects=False, **kwargs
            )
            if response.status_code == 401 and authenticated and (method == "GET"):
                headers["Authorization"] = "Bearer " + self.auth.refresh()
                response = self._client.request(
                    method, url, headers=headers, follow_redirects=False, **kwargs
                )
        except httpx2.HTTPError as exc:
            reason = network_reason(exc)
            raise ServiceNetworkError(
                f"The Customer API request failed ({reason.value}).",
                operation=Operation.SERVICE_REQUEST,
                reason=reason,
            ) from exc
        if not 200 <= response.status_code < 300:
            api_error = None
            if len(response.content) <= 65536:
                try:
                    api_error = ApiErrorResponse.model_validate(response.json())
                except (ValidationError, ValueError, TypeError):
                    pass
            raise ServiceHTTPError(
                f"Customer API request failed (HTTP {response.status_code}).",
                status_code=response.status_code,
                operation=Operation.SERVICE_REQUEST,
                api_error=api_error,
            )
        return response

    def _parse(
        self, response: httpx2.Response, model: type[ResponseModel]
    ) -> ResponseModel:
        try:
            result = model.model_validate(response.json())
        except (ValidationError, ValueError, TypeError) as exc:
            raise ServiceInvalidResponse(
                "The Customer API returned an invalid response.",
                operation=Operation.SERVICE_RESPONSE,
                status_code=response.status_code,
            ) from exc
        order_url = getattr(result, "order_url", None)
        if isinstance(order_url, str):
            try:
                trusted_url(order_url, self.origin)
            except UntrustedURL as exc:
                raise ServiceInvalidResponse(
                    "The Customer API returned an untrusted order URL.",
                    operation=Operation.SERVICE_RESPONSE,
                    status_code=response.status_code,
                ) from exc
        return result

    def get_me(self) -> MeResponse:
        return self._parse(self._request("GET", "/me"), MeResponse)

    def get_options(self) -> OptionsResponse:
        return self._parse(
            self._request("GET", "/options", authenticated=False), OptionsResponse
        )

    def get_constraints(self) -> ManufacturingConstraints:
        return self._parse(
            self._request("GET", "/constraints", authenticated=False),
            ManufacturingConstraints,
        )

    def get_projects(self, organization_id: str | None = None) -> list[ProjectResponse]:
        response = self._request(
            "GET",
            "/projects",
            params={"organization_id": organization_id}
            if organization_id is not None
            else {},
        )
        try:
            data = response.json()
            if not isinstance(data, list):
                raise TypeError()
            return [ProjectResponse.model_validate(item) for item in data]
        except (ValidationError, ValueError, TypeError) as exc:
            raise ServiceInvalidResponse(
                "The Customer API returned invalid projects.",
                operation=Operation.SERVICE_RESPONSE,
                status_code=response.status_code,
            ) from exc

    def create_project(self, request: CreateProjectRequest) -> ProjectCreatedResponse:
        return self._parse(
            self._request(
                "POST",
                "/projects",
                json=request.model_dump(mode="json", exclude_none=True),
            ),
            ProjectCreatedResponse,
        )

    def get_price(self, request: PriceRequest) -> PriceResponse:
        return self._parse(
            self._request(
                "GET",
                "/price",
                authenticated=False,
                params=request.model_dump(mode="json", exclude_none=True),
            ),
            PriceResponse,
        )

    @staticmethod
    def _order_path(order_id: str) -> str:
        if not order_id or order_id in (".", ".."):
            raise ValueError("An order identifier is required.")
        return "/orders/" + quote(order_id, safe="")

    def _upload(
        self,
        path: str,
        file: str | Path,
        parameters: OrderParameters | None,
        fields: dict[str, str],
    ) -> BoardStoredResponse:
        file = Path(file)
        try:
            size = file.stat().st_size
        except OSError as exc:
            raise UploadError(
                "The board file could not be inspected.", operation=Operation.UPLOAD
            ) from exc
        if size > 100 * 1024 * 1024:
            raise UploadTooLarge(
                "Board files must not exceed 100 MiB.", operation=Operation.UPLOAD
            )
        if parameters is not None:
            fields["parameters"] = json.dumps(
                parameters.model_dump(mode="json", exclude_none=True)
            )
        try:
            with file.open("rb") as stream:
                response = self._request(
                    "POST",
                    path,
                    data=fields,
                    files={
                        "file": (
                            file.name,
                            stream,
                            "application/zip"
                            if file.suffix.lower() == ".zip"
                            else "application/octet-stream",
                        )
                    },
                )
        except OSError as exc:
            raise UploadError(
                "The board file could not be read.", operation=Operation.UPLOAD
            ) from exc
        return self._parse(response, BoardStoredResponse)

    def create_order(
        self,
        file: str | Path,
        *,
        parameters: OrderParameters | None = None,
        project_id: str | None = None,
        organization_id: str | None = None,
    ) -> BoardStoredResponse:
        fields = {
            key: value
            for (key, value) in {
                "project_id": project_id,
                "organization_id": organization_id,
            }.items()
            if value is not None
        }
        return self._upload("/orders", file, parameters, fields)

    def add_board(
        self,
        order_id: str,
        file: str | Path,
        *,
        parameters: OrderParameters | None = None,
    ) -> BoardStoredResponse:
        return self._upload(
            self._order_path(order_id) + "/boards", file, parameters, {}
        )

    def get_order(self, order_id: str) -> OrderResponse:
        return self._parse(
            self._request("GET", self._order_path(order_id)), OrderResponse
        )

    def get_order_price(
        self, order_id: str, *, quantity: int | None = None
    ) -> OrderPriceResponse:
        return self._parse(
            self._request(
                "GET",
                self._order_path(order_id) + "/price",
                params={"quantity": quantity} if quantity is not None else {},
            ),
            OrderPriceResponse,
        )

    def delete_order(self, order_id: str) -> None:
        self._request("DELETE", self._order_path(order_id))

    def login(self, timeout: float = 120, *, client_name: str | None = None) -> None:
        if not isinstance(self.auth, BrowserAuth):
            raise UnsupportedAuthOperation(
                "The injected authentication provider manages its own sign-in.",
                operation=Operation.AUTHORIZATION,
            )
        self.auth.login(timeout=timeout, client_name=client_name)

    def logout(self) -> None:
        if not isinstance(self.auth, BrowserAuth):
            raise UnsupportedAuthOperation(
                "The injected authentication provider manages its own sign-out.",
                operation=Operation.REVOCATION,
            )
        self.auth.logout()

    def forget_client(self) -> None:
        if not isinstance(self.auth, BrowserAuth):
            raise UnsupportedAuthOperation(
                "The injected authentication provider manages its own registration.",
                operation=Operation.REVOCATION,
            )
        self.auth.forget_client()

    def close(self) -> None:
        if self._owns_auth and isinstance(self.auth, BrowserAuth):
            self.auth.close()
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
