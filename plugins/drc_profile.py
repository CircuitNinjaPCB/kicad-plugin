from models import ManufacturingConstraints
from pydantic import BaseModel, ConfigDict, Field


class ProfileShape(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    area_mm2: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    holes: int = Field(ge=0)


class ProfileReport(BaseModel):
    version: str
    valid: bool
    shapes: list[ProfileShape]


def profile_messages(
    report: ProfileReport, constraints: ManufacturingConstraints
) -> list[tuple[str, str]]:
    if not report.valid:
        return [
            (
                "board_shape",
                "KiCad could not build a valid board outline. Fix Edge.Cuts before checking size.",
            )
        ]
    shapes = report.shapes
    if not shapes:
        return [
            ("board_shape", "Board profile is empty. Exactly one shape is required.")
        ]
    main = max(shapes, key=lambda shape: shape.area_mm2)
    messages: list[tuple[str, str]] = []
    if len(shapes) != 1:
        messages.append(
            (
                "board_shape",
                f"Found {len(shapes)} separate board shapes. Only one is allowed. Size checks use the largest shape.",
            )
        )
    (width, height) = (main.width_mm, main.height_mm)
    (minimum, maximum) = (constraints.board.min_mm, constraints.board.max_mm)
    eps = 0.001
    for name, limits, valid in (
        (
            "minimum",
            minimum,
            any(
                width > a - eps and height > b - eps
                for (a, b) in (minimum, minimum[::-1])
            ),
        ),
        (
            "maximum",
            maximum,
            any(
                width < a + eps and height < b + eps
                for (a, b) in (maximum, maximum[::-1])
            ),
        ),
    ):
        if not valid:
            messages.append(
                (
                    "board_size",
                    f"Board violates {name} size: {width:.3f} × {height:.3f} mm; {name} {limits[0]:g} × {limits[1]:g} mm (either orientation).",
                )
            )
    return messages
