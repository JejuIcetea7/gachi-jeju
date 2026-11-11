import hashlib
from pathlib import Path

from ..tools.places import read_rows

PROJECT_ROOT = Path(__file__).resolve().parents[3]
IMAGE_ROOT = PROJECT_ROOT / "data" / "raw" / "jeju_datahub"

IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def resolve_image_path(source_file: str, image_file: str) -> Path | None:
    if not isinstance(source_file, str) or not isinstance(image_file, str):
        return None
    if not source_file.strip() or not image_file.strip():
        return None

    try:
        root = IMAGE_ROOT.resolve()
        source = (PROJECT_ROOT / source_file).resolve()
        directory = source.parent
        image = (directory / image_file).resolve()

        if (
            not directory.is_relative_to(root)
            or not image.is_relative_to(directory)
            or image.suffix.lower() not in IMAGE_TYPES
            or not image.is_file()
        ):
            return None

        return image
    except (OSError, ValueError, RuntimeError):
        return None


def list_place_images(place_id: str) -> dict | None:
    """DB에 기록된 원본 폴더에 실제로 있는 사진만 반환합니다."""
    places = read_rows(
        "SELECT place_id, name FROM places WHERE place_id = ?",
        (place_id,),
    )
    if not places:
        return None

    place = places[0]
    rows = read_rows(
        'SELECT name, image_file, source_file FROM place_spots '
        'WHERE place_id = ? ORDER BY "order", source_file, image_file',
        (place_id,),
    )

    images = []
    seen = set()

    for row in rows:
        path = resolve_image_path(row["source_file"], row["image_file"])
        if path is None or path in seen:
            continue
        seen.add(path)

        image_id = hashlib.sha256(
            f'{row["source_file"]}\0{row["image_file"]}'.encode("utf-8")
        ).hexdigest()
        caption = row["name"] or place["name"]

        images.append({
            "image_id": image_id,
            "path": path,
            "media_type": IMAGE_TYPES[path.suffix.lower()],
            "caption": caption,
            "alt": f'{place["name"]} · {caption}',
        })

    return {
        "place_id": place["place_id"],
        "name": place["name"],
        "images": images,
    }