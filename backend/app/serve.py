from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .main import app as api_app, lifespan

frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/api", api_app)
app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")