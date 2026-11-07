from ..config import get_kakao_api_key
from ..services.kakao_local import search_keyword_place

def find_location(*, keyword: str) -> dict:
    """제주도 안에서 장소 이름(예: 제주국제공항, 숙소 이름)을 검색해 이름, 주소, 위도, 경도를 돌려줍니다.
    출발지처럼 좌표를 모르는 장소의 좌표를 구할 때 씁니다."""
    return search_keyword_place(
        keyword=keyword,
        api_key=get_kakao_api_key(),
    )
