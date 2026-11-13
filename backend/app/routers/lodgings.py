from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import ValidationError

from ..config import get_kakao_api_key
from ..services.lodging_models import LodgingSearchResponse
from ..services.lodgings import (
    MAX_LATITUDE, MAX_LONGITUDE, MAX_RADIUS_M, MAX_RESULTS, MIN_LATITUDE, MIN_LONGITUDE,
    search_nearby_lodgings,
)
from .plan import ErrorResponse, build_error_response


router = APIRouter(prefix="/lodgings", tags=["숙소 후보"])
Latitude = Annotated[float, Query(ge=MIN_LATITUDE, le=MAX_LATITUDE, allow_inf_nan=False)]
Longitude = Annotated[float, Query(ge=MIN_LONGITUDE, le=MAX_LONGITUDE, allow_inf_nan=False)]
Radius = Annotated[int, Query(ge=1, le=MAX_RADIUS_M)]
ResultCount = Annotated[int, Query(ge=1, le=MAX_RESULTS)]


def _search_error():
    return build_error_response(
        502, "lodging_search_failed", "주변 숙소를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요."
    )


@router.get(
    "/nearby",
    response_model=LodgingSearchResponse,
    responses={
        400: {"model": ErrorResponse, "description": "검색 위치·반경·결과 수 입력 오류"},
        429: {"model": ErrorResponse, "description": "숙소 검색 한도 초과"},
        502: {"model": ErrorResponse, "description": "숙소 검색 응답 오류"},
        503: {"model": ErrorResponse, "description": "숙소 검색 설정 오류"},
    },
)
def get_nearby_lodgings(
    latitude: Latitude,
    longitude: Longitude,
    radius_m: Radius = 5000,
    max_count: ResultCount = 5,
):
    result = search_nearby_lodgings(
        latitude=latitude, longitude=longitude, radius_m=radius_m, max_count=max_count,
        api_key=get_kakao_api_key(),
    )
    if not isinstance(result, dict):
        return _search_error()
    status = result.get("status")
    if status == "rate_limited":
        return build_error_response(
            429, "lodging_search_rate_limited", "숙소 검색 요청이 많습니다. 잠시 후 다시 시도해 주세요."
        )
    if status == "auth_error":
        return build_error_response(
            503, "lodging_search_unavailable", "현재 숙소 검색을 사용할 수 없습니다."
        )
    if status == "invalid_input":
        return build_error_response(400, "invalid_request", "숙소 검색 위치와 범위를 확인해 주세요.")
    if status not in ("ok", "not_found"):
        return _search_error()

    try:
        response = LodgingSearchResponse.model_validate(result)
    except ValidationError:
        return _search_error()
    if (
        response.center.latitude != latitude
        or response.center.longitude != longitude
        or response.radius_m != radius_m
        or len(response.places) > max_count
        or bool(response.places) != (status == "ok")
    ):
        return _search_error()
    return response.model_dump(mode="json")
