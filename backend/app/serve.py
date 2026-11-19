import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .main import app as api_app, lifespan

frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/map-config", include_in_schema=False)
def get_map_config():
    return JSONResponse(
        {"kakao_map_key": os.environ.get("VITE_KAKAO_MAP_KEY", "").strip()},
        headers={"Cache-Control": "no-store"},
    )


app.mount("/api", api_app)
app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")