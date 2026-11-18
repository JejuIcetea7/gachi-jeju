import sqlite3
from stat import S_ISREG
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from ..services.place_representative_image import get_representative_image
from pydantic import BaseModel

from ..services.place_images import list_place_images
from ..services.cloud_photos import PhotoCatalogError, validate_cdn_photo_url
from .plan import ErrorResponse, build_error_response

router = APIRouter(prefix="/places", tags=["장소 사진"])

ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "장소 또는 사진 없음"},
    500: {"model": ErrorResponse, "description": "사진 정보 조회 오류"},
}


class PlaceImageContext(BaseModel):
    spot_name: str
    description: str
    mapping_confirmed: bool


class PlaceImageResponse(BaseModel):
    image_id: str
    url: str
    caption: str
    alt: str
    photo_id: str | None = None
    contexts: list[PlaceImageContext] | None = None


class PlaceImagesResponse(BaseModel):
    place_id: str
    name: str
    images: list[PlaceImageResponse]


def _read_images(place_id: str):
    try:
        result = list_place_images(place_id)
    except (sqlite3.Error, PhotoCatalogError):
        return build_error_response(
            500, "internal_error", "사진 정보를 불러오지 못했습니다."
        )

    if result is None:
        return build_error_response(
            404, "place_not_found", "장소 정보를 찾지 못했습니다."
        )

    return result

@router.get(
    "/{place_id}/representative-image",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}}},
        404: {"description": "대표 사진 없음"},
    },
)
def get_place_representative_image(place_id: str):
    content = get_representative_image(place_id)
    if content is None:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    return Response(
        content=content,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )

@router.get(
    "/{place_id}/images",
    response_model=PlaceImagesResponse,
    response_model_exclude_none=True,
    responses=ERROR_RESPONSES,
)
def get_place_images(place_id: str):
    result = _read_images(place_id)
    if isinstance(result, JSONResponse):
        return result

    encoded_id = quote(result["place_id"], safe="")

    return {
        "place_id": result["place_id"],
        "name": result["name"],
        "images": [
            {
                "image_id": image["image_id"],
                "url": image.get("cdn_url") or f'/places/{encoded_id}/images/{image["image_id"]}',
                "caption": image["caption"],
                "alt": image["alt"],
                **({"photo_id": image["photo_id"], "contexts": image["contexts"]}
                   if "cdn_url" in image else {}),
            }
            for image in result["images"]
        ],
    }


@router.get(
    "/{place_id}/images/{image_id}",
    response_class=FileResponse,
    responses=ERROR_RESPONSES,
)
def get_place_image(place_id: str, image_id: str):
    result = _read_images(place_id)
    if isinstance(result, JSONResponse):
        return result

    for image in result["images"]:
        if image["image_id"] != image_id:
            continue

        if "cdn_url" in image:
            try:
                url = validate_cdn_photo_url(image["cdn_url"], image["photo_id"])
            except PhotoCatalogError:
                return build_error_response(500, "internal_error", "사진 정보를 불러오지 못했습니다.")
            return RedirectResponse(url, status_code=307, headers={"X-Content-Type-Options": "nosniff"})

        try:
            image_stat = image["path"].stat()
            if not S_ISREG(image_stat.st_mode):
                break
        except OSError:
            break

        return FileResponse(
            image["path"],
            media_type=image["media_type"],
            stat_result=image_stat,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    return build_error_response(
        404, "image_not_found", "사진을 찾지 못했습니다."
    )