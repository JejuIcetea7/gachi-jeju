from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from ..config import get_kakao_api_key
from ..services.kakao_mobility import get_driving_route


def create_driving_map_tool(route_calls: list[dict]) -> Callable[..., dict]:
    """요청별 경로 기록을 사용하는 자동차 도구를 만듭니다."""

    def get_driving_time(
        *,
        origin_latitude: float,
        origin_longitude: float,
        destination_latitude: float,
        destination_longitude: float,
    ) -> dict:
        """두 지점 사이의 자동차 거리(m)와 예상 소요시간(초)을 조회합니다.

        route_id는 이 조회 결과를 식별하는 값입니다.
        """
        requested_at = datetime.now(timezone.utc).isoformat()

        result = get_driving_route(
            origin_latitude=origin_latitude,
            origin_longitude=origin_longitude,
            destination_latitude=destination_latitude,
            destination_longitude=destination_longitude,
            api_key=get_kakao_api_key(),
            include_geometry=True,
        )

        if result["status"] != "ok":
            return result

        route_id = str(uuid4())
        route_calls.append({
            "route_id": route_id,
            "transport": "driving",
            "origin": {
                "latitude": origin_latitude,
                "longitude": origin_longitude,
            },
            "destination": {
                "latitude": destination_latitude,
                "longitude": destination_longitude,
            },
            "distance_m": result["distance_m"],
            "duration_seconds": result["duration_seconds"],
            "geometry_status": result["geometry_status"],
            "paths": deepcopy(result["paths"]),
            "requested_at": requested_at,
        })

        return {
            "status": result["status"],
            "distance_m": result["distance_m"],
            "duration_seconds": result["duration_seconds"],
            "route_id": route_id,
        }

    return get_driving_time