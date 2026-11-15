from pydantic import BaseModel, Field


class HotspotModelInfo(BaseModel):
    region: str = Field(description="Region the model was trained on.")
    trained_months: list[str] = Field(description="First and last month (YYYY-MM) of the training rows.")
    features: list[str] = Field(description="Model input features.")


class HotspotForecast(BaseModel):
    month: str = Field(description="Forecast month (YYYY-MM) containing the requested date.")
    lat: float = Field(description="Requested latitude.")
    lon: float = Field(description="Requested longitude.")
    pixel_lat: float = Field(description="Centre latitude of the 500 m pixel used.")
    pixel_lon: float = Field(description="Centre longitude of the 500 m pixel used.")
    hotspot_probability: float = Field(description="Estimated probability of at least one FIRMS vegetation-fire hotspot in the pixel during the month.")
    hotspot_score: float = Field(description="Raw model output on the training sample scale (1 hotspot : 11 no-hotspot rows); use for ranking only.")
    features: dict[str, float | int | None] = Field(description="Feature values the model received.")
    model: HotspotModelInfo
