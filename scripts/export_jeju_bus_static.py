import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.services.jeju_bus import get_bus_routes, get_bus_route_stops

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "processed" / "bus"

def save_rows(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise SystemExit(f"응답이 비어 있어 저장을 중단합니다. {path.name}")

    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as temp_dir:
        temp_path = Path(temp_dir) / path.name
        temp_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)

    print(f"{path.relative_to(OUTPUT_DIR)} 저장 ({len(rows)}건)")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("route_ids", nargs="*", type=int)
    parser.add_argument("--route-numbers", nargs="+", default=[])
    args = parser.parse_args()

    if not args.route_ids and not args.route_numbers:
        parser.error("노선 ID 또는 --route-numbers 값을 입력해 주세요.")

    catalog_path = OUTPUT_DIR / "routes.json"
    if args.route_numbers and catalog_path.is_file():
        routes = json.loads(catalog_path.read_text(encoding="utf-8"))
    else:
        routes = get_bus_routes()
        save_rows(catalog_path, routes)

    explicit_ids = set(args.route_ids)
    route_ids = explicit_ids.copy()
    requested_numbers = set(args.route_numbers)
    found_numbers = set()

    for route in routes:
        number = str(route.get("ROUTE_NUM", "")).strip()
        if number in requested_numbers:
            route_ids.add(int(route["ROUTE_ID"]))
            found_numbers.add(number)

    missing_numbers = requested_numbers - found_numbers
    if missing_numbers:
        parser.error(f"노션 목록에서 찾지 못한 번호: {', '.join(sorted(missing_numbers))}")

    for route_id in sorted(route_ids):
        path = OUTPUT_DIR / "route_stops" / f"{route_id}.json"
        if route_id not in explicit_ids and path.is_file():
            print(f"{path.name} 기존 파일 사용")
            continue
        save_rows(path, get_bus_route_stops(route_id))

if __name__ == "__main__":
    main()