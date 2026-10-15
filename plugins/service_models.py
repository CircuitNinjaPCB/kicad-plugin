from datetime import date, datetime
from enum import Enum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrganizationRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


OrganizationRoleValue = Annotated[
    OrganizationRole | str, Field(union_mode="left_to_right")
]


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


ProjectStatusValue = Annotated[ProjectStatus | str, Field(union_mode="left_to_right")]


class CheckState(str, Enum):
    NOT_STARTED = "not_started"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


CheckStateValue = Annotated[CheckState | str, Field(union_mode="left_to_right")]


class ProblemLevel(str, Enum):
    WARNING = "warning"
    ERROR = "error"


ProblemLevelValue = Annotated[ProblemLevel | str, Field(union_mode="left_to_right")]


class PcbColor(str, Enum):
    GREEN = "green"
    WHITE = "white"
    BLACK = "black"
    RED = "red"
    BLUE = "blue"


PcbColorValue = Annotated[PcbColor | str, Field(union_mode="left_to_right")]


class SurfaceFinish(str, Enum):
    HASL_LEAD = "hasl-lead"
    HASL_NO_LEAD = "hasl-no-lead"
    ENIG = "enig"
    IMMERSION_SILVER = "immersion-silver"
    IMMERSION_TIN = "immersion-tin"


SurfaceFinishValue = Annotated[SurfaceFinish | str, Field(union_mode="left_to_right")]


class ElectricTest(str, Enum):
    FLYING_PROBE = "flying-probe"
    NO_TEST = "no-test"


ElectricTestValue = Annotated[ElectricTest | str, Field(union_mode="left_to_right")]


class BaseMaterial(str, Enum):
    FR4 = "fr-4"


BaseMaterialValue = Annotated[BaseMaterial | str, Field(union_mode="left_to_right")]


class CopperThickness(str, Enum):
    UM25 = "25um"
    UM35 = "35um"
    UM70 = "70um"


CopperThicknessValue = Annotated[
    CopperThickness | str, Field(union_mode="left_to_right")
]


class Thickness(str, Enum):
    MM04 = "0.4"
    MM06 = "0.6"
    MM08 = "0.8"
    MM10 = "1.0"
    MM12 = "1.2"
    MM16 = "1.6"
    MM20 = "2.0"


ThicknessValue = Annotated[Thickness | str, Field(union_mode="left_to_right")]


class Currency(str, Enum):
    CZK = "CZK"


CurrencyValue = Annotated[Currency | str, Field(union_mode="left_to_right")]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]
NonnegativeInt = Annotated[int, Field(ge=0, strict=True)]
PositiveNumber = Annotated[float, Field(gt=0, allow_inf_nan=False)]
OrganizationId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class User(ResponseModel):
    id: str
    name: str
    email: str


class Organization(ResponseModel):
    id: str
    name: str
    slug: str
    role: OrganizationRoleValue
    can_order: bool


class MeResponse(ResponseModel):
    user: User
    can_order: bool
    organizations: list[Organization]


class OptionLabels(ResponseModel):
    pcb_color: dict[str, str]
    silkscreen_color: dict[str, str]
    surface_finish: dict[str, str]
    electric_test: dict[str, str]
    paper_between_pcbs: dict[str, str]


class OptionsResponse(ResponseModel):
    layer_count: list[int]
    thickness: list[ThicknessValue]
    base_material: list[BaseMaterialValue]
    silkscreen_color: list[PcbColorValue]
    pcb_color: list[PcbColorValue]
    surface_finish: list[SurfaceFinishValue]
    electric_test: list[ElectricTestValue]
    paper_between_pcbs: list[bool]
    copper_thickness_by_layer_count: dict[str, list[CopperThicknessValue]]
    max_production_days: int
    labels: dict[str, OptionLabels]


class ProjectOrganization(ResponseModel):
    id: str
    name: str


class ProjectResponse(ResponseModel):
    id: str
    name: str
    description: str | None
    status: ProjectStatusValue
    organization: ProjectOrganization | None
    created_at: datetime | None


class CreateProjectRequest(RequestModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    description: str | None = Field(default=None, max_length=2000)
    organization_id: OrganizationId | None = None


class ProjectCreatedResponse(ResponseModel):
    id: str


class OrderParameters(RequestModel):
    qty: PositiveInt | None = None
    pcb_color: PcbColorValue | None = None
    silkscreen_color: PcbColorValue | None = None
    surface_finish: SurfaceFinishValue | None = None
    thickness: ThicknessValue | None = None
    copper_thickness: CopperThicknessValue | None = None
    base_material: BaseMaterialValue | None = None
    electric_test: ElectricTestValue | None = None
    edge_plating: bool | None = None
    castellated_holes: bool | None = None
    paper_between_pcbs: bool | None = None
    pcb_remarks: str | None = None
    stencil_top_qty: NonnegativeInt | None = None
    stencil_bottom_qty: NonnegativeInt | None = None


class PriceRequest(RequestModel):
    layer_count: PositiveInt
    dimensionsX: PositiveNumber
    dimensionsY: PositiveNumber
    qty: PositiveInt
    leadTime: PositiveInt
    vias: NonnegativeInt | None = None


class OfferViolation(ResponseModel):
    field: str
    value: str | int | float | bool
    allowed: list[str | int | float | bool]
    item_index: int | None = None


class OrderableVerdict(ResponseModel):
    ok: bool
    violations: list[OfferViolation] = Field(default_factory=list)


class PriceResponse(ResponseModel):
    currency: CurrencyValue
    amount_without_tax: float
    vat: float
    total: float
    per_board: float
    orderable: OrderableVerdict


class BoardStoredResponse(ResponseModel):
    order_id: str
    item_id: str
    file_id: str
    order_url: str


class DetectedBoard(ResponseModel):
    layer_count: int | None
    dimensions_x: float | None
    dimensions_y: float | None
    min_hole_size: str | None
    board_area_mm2: float | None


class BoardProblem(ResponseModel):
    component: str | None
    level: ProblemLevelValue
    message: str
    fix: str


class BoardCheck(ResponseModel):
    item_id: str
    state: CheckStateValue
    blocking: bool
    detected: DetectedBoard | None
    problems: list[BoardProblem]


class OrderResponse(ResponseModel):
    pretty_id: str | None
    order_url: str
    blocking: bool
    boards: list[BoardCheck]


class BoardPrice(ResponseModel):
    item_id: str
    qty: int
    total: float
    vat: float
    per_board: float


class PriceRow(ResponseModel):
    ready_date: date
    production_days: int
    lead_time_hours: int
    at_risk: bool
    total: float
    vat: float
    per_board: float | None = None
    boards: list[BoardPrice]


class OrderPriceResponse(ResponseModel):
    currency: CurrencyValue
    quantity: int
    orderable: OrderableVerdict
    rows: list[PriceRow]


class ApiErrorResponse(ResponseModel):
    error: str | None = None
    message: str | None = None
    statusCode: int | None = None
    violations: list[OfferViolation] = Field(default_factory=list)


class Scope(str, Enum):
    DRAFTING = "drafting"
    ORDERING = "ordering"
    CONTENT = "content"
    OFFLINE_ACCESS = "offline_access"


ScopeValue = Annotated[Scope | str, Field(union_mode="left_to_right")]


class GrantType(str, Enum):
    AUTHORIZATION_CODE = "authorization_code"
    REFRESH_TOKEN = "refresh_token"


GrantTypeValue = Annotated[GrantType | str, Field(union_mode="left_to_right")]


class ResponseType(str, Enum):
    CODE = "code"


ResponseTypeValue = Annotated[ResponseType | str, Field(union_mode="left_to_right")]


class CodeChallengeMethod(str, Enum):
    S256 = "S256"


CodeChallengeMethodValue = Annotated[
    CodeChallengeMethod | str, Field(union_mode="left_to_right")
]


class TokenEndpointAuthMethod(str, Enum):
    NONE = "none"
    CLIENT_SECRET_BASIC = "client_secret_basic"
    CLIENT_SECRET_POST = "client_secret_post"


TokenEndpointAuthMethodValue = Annotated[
    TokenEndpointAuthMethod | str, Field(union_mode="left_to_right")
]
REQUIRED_SCOPES = (Scope.DRAFTING, Scope.OFFLINE_ACCESS)


class ProtectedResourceMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    resource: str
    authorization_servers: list[str] = Field(min_length=1)
    scopes_supported: list[ScopeValue] = Field(default_factory=list)
    bearer_methods_supported: list[str] = Field(default_factory=list)
    jwks_uri: str | None = None


class AuthorizationServerMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    issuer: str
    registration_endpoint: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str
    scopes_supported: list[ScopeValue] = Field(default_factory=list)
    grant_types_supported: list[GrantTypeValue] = Field(default_factory=list)
    response_types_supported: list[ResponseTypeValue] = Field(default_factory=list)
    code_challenge_methods_supported: list[CodeChallengeMethodValue] = Field(
        default_factory=list
    )
    token_endpoint_auth_methods_supported: list[TokenEndpointAuthMethodValue] = Field(
        default_factory=list
    )
    userinfo_endpoint: str | None = None
    jwks_uri: str | None = None


DEFAULT_CLIENT_NAME = "CircuitNinja for KiCad"


def validate_client_name(value: str) -> str:
    if value == DEFAULT_CLIENT_NAME:
        return value
    prefix = DEFAULT_CLIENT_NAME + ": "
    suffix = value.removeprefix(prefix)
    if (
        not value.startswith(prefix)
        or not suffix
        or suffix != suffix.strip()
        or any(not character.isprintable() for character in value)
    ):
        raise ValueError(
            f"Client name must be '{DEFAULT_CLIENT_NAME}' or '{DEFAULT_CLIENT_NAME}: <custom text>'."
        )
    return value


ClientName = Annotated[str, AfterValidator(validate_client_name)]


class ClientRegistrationRequest(RequestModel):
    client_name: ClientName = DEFAULT_CLIENT_NAME
    redirect_uris: list[str] = Field(min_length=1)
    token_endpoint_auth_method: TokenEndpointAuthMethodValue = (
        TokenEndpointAuthMethod.NONE
    )
    grant_types: list[GrantTypeValue] = Field(
        default_factory=lambda: [GrantType.AUTHORIZATION_CODE, GrantType.REFRESH_TOKEN]
    )
    response_types: list[ResponseTypeValue] = Field(
        default_factory=lambda: [ResponseType.CODE]
    )
    scope: str = " ".join(scope.value for scope in REQUIRED_SCOPES)


class AuthorizationRequest(RequestModel):
    client_id: str
    redirect_uri: str
    state: str = Field(repr=False)
    code_challenge: str
    resource: str
    response_type: ResponseTypeValue = ResponseType.CODE
    code_challenge_method: CodeChallengeMethodValue = CodeChallengeMethod.S256
    scope: str = " ".join(scope.value for scope in REQUIRED_SCOPES)


class CodeExchangeRequest(RequestModel):
    client_id: str
    redirect_uri: str
    resource: str
    code: str = Field(repr=False)
    code_verifier: str = Field(repr=False)
    grant_type: GrantTypeValue = GrantType.AUTHORIZATION_CODE


class RefreshTokenRequest(RequestModel):
    client_id: str
    resource: str
    refresh_token: str = Field(repr=False)
    grant_type: GrantTypeValue = GrantType.REFRESH_TOKEN


class OAuthEndpoints(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    resource: str
    issuer: str
    registration_endpoint: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str


class ClientRegistrationResponse(ResponseModel):
    client_id: str


class TokenResponse(ResponseModel):
    access_token: str = Field(min_length=1, repr=False)
    token_type: str
    expires_in: PositiveInt
    refresh_token: str | None = Field(default=None, repr=False)
    scope: str | None = None
