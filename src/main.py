from fastapi import FastAPI
from src.models.response import ApiResponse
from src.routers.api_nasa import fires, health, quota, sources
from src.utils.pyproject_variables import name, version, description


app = FastAPI()
app.include_router(health.router)
app.include_router(quota.router)
app.include_router(sources.router)
app.include_router(fires.router)


@app.get("/")
async def root() -> ApiResponse[dict]:
    return ApiResponse(
        data={
            "message": f"Welcome to {name}",
            "version": version,
            "description": description,
        }
    )
