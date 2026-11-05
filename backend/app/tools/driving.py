from ..config import get_kakao_api_key
from ..services.kakao_mobility import get_driving_route

def get_driving_time(
    *,
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
) -> dict[str, str | int]:
    """두 지점 사이의 자동차 이동 거리(m)와 예상 소요시간(초)을 조회합니다."""
    return get_driving_route(
        origin_latitude=origin_latitude,
        origin_longitude=origin_longitude,
        destination_latitude=destination_latitude,
        destination_longitude=destination_longitude,
        api_key=get_kakao_api_key(),
    )