FROM node:24-bookworm-slim AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY data/processed/ ./data/processed/
COPY --from=frontend /build/dist/ ./frontend/dist/

EXPOSE 10000
CMD ["python", "-m", "uvicorn", "backend.app.serve:app", "--host", "0.0.0.0", "--port", "10000"]