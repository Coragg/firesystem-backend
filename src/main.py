from fastapi import FastAPI
from src.models.response import ApiResponse


app = FastAPI()

@app.get("/")
async def root() -> ApiResponse[dict]:
    return ApiResponse(data={"message": "Welcome to NASA"})
