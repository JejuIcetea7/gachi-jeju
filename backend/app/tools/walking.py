from ..config import get_kakao_api_key
from ..services.kakao_walking import get_walking_route as fetch_walking_route

def get_walking_route(
    *,
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
    route_mode: str = "BROAD_FIRST",
) -> dict:
    """두 지점 사이의 도보 거리, 예상 이동시간과 경로 안내를 조회합니다."""
    return fetch_walking_route(
        origin_latitude=origin_latitude,
        origin_longitude=origin_longitude,
        destination_latitude=destination_latitude,
        destination_longitude=destination_longitude,
        api_key=get_kakao_api_key(),
        route_mode=route_mode,
    )