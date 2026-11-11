from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from ..config import get_kakao_api_key
from ..services.kakao_mobility import get_driving_route

from ..services.kakao_walking import get_walking_route as fetch_walking_route
from ..services.walking_geometry import extract_walking_geometry
from ..services.kakao_transit import get_transit_routes
from .bus import _build_bus_route_summary

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

def create_walking_map_tool(route_calls: list[dict]) -> Callable[..., dict]:
    """요청별 경로 기록을 사용하는 도보 도구를 만듭니다."""

    def get_walking_route(
        *,
        origin_latitude: float,
        origin_longitude: float,
        destination_latitude: float,
        destination_longitude: float,
        route_mode: str = "BROAD_FIRST",
    ) -> dict:
        """두 지점 사이의 도보 거리(m), 예상 시간(초)과 안내를 조회합니다.

        route_id는 이번 조회 결과를 식별합니다.
        ACCESSIBLE 옵션도 실제 휠체어 통행 가능 여부를 보장하지 않습니다.
        """
        requested_at = datetime.now(timezone.utc).isoformat()

        result = fetch_walking_route(
            origin_latitude=origin_latitude,
            origin_longitude=origin_longitude,
            destination_latitude=destination_latitude,
            destination_longitude=destination_longitude,
            api_key=get_kakao_api_key(),
            route_mode=route_mode,
        )

        if result["status"] != "ok":
            return result

        steps = result.get("steps")
        geometry = extract_walking_geometry(steps)
        route_id = str(uuid4())

        route_calls.append({
            "route_id": route_id,
            "transport": "walking",
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
            **geometry,
            "requested_at": requested_at,
        })

        guidance_steps = [
            {
                key: step.get(key)
                for key in ("distance_m", "duration_seconds", "guidance")
            }
            for step in (steps if isinstance(steps, list) else [])
            if isinstance(step, dict)
        ]

        return {
            "status": result["status"],
            "distance_m": result["distance_m"],
            "duration_seconds": result["duration_seconds"],
            "landing_url": result.get("landing_url"),
            "steps": guidance_steps,
            "route_id": route_id,
        }

    return get_walking_route

def create_bus_map_tool(route_calls: list[dict]) -> Callable[..., dict]:
    """요청별 경로 기록을 사용하는 버스 도구를 만듭니다."""

    def get_bus_route_summary(
        *,
        origin_latitude: float,
        origin_longitude: float,
        destination_latitude: float,
        destination_longitude: float,
    ) -> dict:
        """버스 경로 하나와 예상 이동시간(초), 구간별 버스 정보를 조회합니다.

        환승이 가장 적고, 같으면 빠른 경로를 선택합니다.
        현재 조회된 도착 예정 차량의 저상 여부는 미래 탑승을 보장하지 않습니다.
        route_id는 이번 조회 결과를 식별합니다.
        """
        requested_at = datetime.now(timezone.utc).isoformat()

        result = get_transit_routes(
            origin_latitude=origin_latitude,
            origin_longitude=origin_longitude,
            destination_latitude=destination_latitude,
            destination_longitude=destination_longitude,
            api_key=get_kakao_api_key(),
            include_geometry=True,
        )
        if result["status"] != "ok":
            return result

        summary, route = _build_bus_route_summary(result)
        route_id = str(uuid4())

        route_calls.append({
            "route_id": route_id,
            "transport": "transit",
            "origin": {
                "latitude": origin_latitude,
                "longitude": origin_longitude,
            },
            "destination": {
                "latitude": destination_latitude,
                "longitude": destination_longitude,
            },
            "distance_m": route["distance_m"],
            "duration_seconds": route["duration_seconds"],
            "geometry_status": route.get("geometry_status", "missing"),
            "paths": deepcopy(route.get("paths") or []),
            "requested_at": requested_at,
        })

        return {**summary, "route_id": route_id}

    return get_bus_route_summary