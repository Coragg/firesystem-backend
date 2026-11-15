"""[AI] `GET /api/v1/sources`: where the loaded data comes from and how each sensor is scaled."""

from fastapi import APIRouter

from src.models.response import ApiResponse, Meta
from src.models.source import CalibrationInfo, Coverage, ProductInfo, SatelliteInfo, SourcesData, Validation
from src.utils import db
from src.utils.coverage import AREAS
from src.utils.harmonize import validation_error
from src.utils.supabase import SupabaseDep

router = APIRouter(prefix="/api/v1", tags=["Database"])


@router.get("/sources")
def sources_view(client: SupabaseDep) -> ApiResponse[SourcesData]:
    """Where the data comes from, how each sensor is scaled and how well the scaling holds."""
    coverage = {r["satellite"]: r for r in db.rpc(client, "satellite_coverage")}
    factors = db.select(client, "calibration_factor", "reference,month,k,overlap_cell_days,is_estimated")
    k = {(f["reference"], f["month"]): f["k"] for f in factors}
    error = validation_error(db.rpc(client, "calibration_check"), k)

    def role(s: dict) -> str:
        """[AI] priority 1 (S-NPP) is the reference, MODIS is scaled to it, other VIIRS satellites fill gaps."""
        return "reference" if s["priority"] == 1 else "scaled" if s["instrument"] == "MODIS" else "fallback"

    return ApiResponse(
        data=SourcesData(
            satellites=[
                SatelliteInfo(
                    code=s["code"], instrument=s["instrument"], pixel_m=s["pixel_m"], role=role(s),
                    first_date=coverage.get(s["code"], {}).get("first_date"),
                    last_date=coverage.get(s["code"], {}).get("last_date"),
                )
                for s in db.select(client, "satellite", "code,instrument,pixel_m,priority")
            ],
            products=[ProductInfo(**p) for p in db.select(client, "firms_source", "code,kind,min_date,max_date")],
            calibration=[
                CalibrationInfo(reference=f["reference"], month=f["month"], k=f["k"],
                                overlap_cell_days=f["overlap_cell_days"], estimated=f["is_estimated"])
                for f in factors
            ],
            validation=Validation(reference="modis", mean_abs_error_pct=error) if error is not None else None,
            coverage=Coverage(name="Chile", bbox=AREAS["mainland"]),
        ),
        meta=Meta(warnings=[] if factors else ["CALIBRATION_MISSING"]),
    )
