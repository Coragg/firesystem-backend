from fastapi import FastAPI
from src.models.response import ApiResponse
from src.utils.pyproject_variables import name, version, description


app = FastAPI()


@app.get("/")
async def root() -> ApiResponse[dict]:
    return ApiResponse(
        data={
            "message": f"Welcome to {name}",
            "version": version,
            "description": description,
        }
    )
