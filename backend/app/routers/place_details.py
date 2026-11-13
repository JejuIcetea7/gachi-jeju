import sqlite3
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints, ValidationError

from ..tools.places import get_place_details as read_place_details
from .plan import ErrorResponse, build_error_response


router = APIRouter(prefix="/places", tags=["장소 상세"])

PlaceId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
]


class PlaceSpotResponse(BaseModel):
    name: str | None
    description: str


class AccessibilityFactResponse(BaseModel):
    section: str | None
    facility_name: str
    description: str | None
    raw_value: str


class PlaceDetailsResponse(BaseModel):
    place_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1)
    category: str | None
    closed_days: str
    spots: list[PlaceSpotResponse]
    accessibility_facts: list[AccessibilityFactResponse]


def _detail_error():
    return build_error_response(
        500, "internal_error", "장소 상세 정보를 불러오지 못했습니다."
    )


@router.get(
    "/{place_id}/details",
    response_model=PlaceDetailsResponse,
    responses={
        400: {"model": ErrorResponse, "description": "입력 오류"},
        404: {"model": ErrorResponse, "description": "장소 없음"},
        500: {"model": ErrorResponse, "description": "장소 정보 조회 오류"},
    },
)
def get_place_details(place_id: PlaceId):
    try:
        result = read_place_details(place_id=place_id)
    except sqlite3.Error:
        return _detail_error()

    if not isinstance(result, dict):
        return _detail_error()

    if result.get("status") == "not_found":
        return build_error_response(
            404, "place_not_found", "장소 정보를 찾지 못했습니다."
        )

    if result.get("status") != "ok":
        return _detail_error()

    try:
        detail = PlaceDetailsResponse.model_validate(result, strict=True)
    except ValidationError:
        return _detail_error()

    if detail.place_id != place_id:
        return _detail_error()

    return detail.model_dump()
