import json
from pathlib import Path

BUS_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "processed" / "bus"

def _read_rows(path: Path) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"버스 데이터 파일 형식 오류 : {path.name}")
    return rows

def load_bus_data(
    data_dir: Path = BUS_DATA_DIR
) -> tuple[list[dict], dict[str, list[dict]]]:
    routes = _read_rows(data_dir / "routes.json")
    stops_by_route = {
        path.stem: _read_rows(path)
        for path in sorted((data_dir / "route_stops").glob("*.json"))
    }
    return routes, stops_by_route