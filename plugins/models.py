from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Millimeters = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Board(BaseModel):
    min_mm: tuple[Millimeters, Millimeters]
    max_mm: tuple[Millimeters, Millimeters]

    @model_validator(mode="after")
    def ordered_dimensions(self) -> "Board":
        if any(low > high for (low, high) in zip(self.min_mm, self.max_mm)):
            raise ValueError(
                "Board minimum dimensions must not exceed maximum dimensions"
            )
        return self


class ManufacturingConstraints(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: Literal[1] = Field(default=1, frozen=True)
    board: Board
    min_hole_to_edge_mm: Millimeters
    min_hole_to_hole_mm: Millimeters
    min_annular_ring_mm: Millimeters
    min_trace_width_mm: Millimeters
    min_clearance_mm: Millimeters
    min_pth_mm: Millimeters
    min_npth_mm: Millimeters

    @classmethod
    def from_path(cls, path: str | Path) -> "ManufacturingConstraints":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
