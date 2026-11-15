"""콘솔에 업로드한 사진의 CloudFront 주소 CSV를 만듭니다. AWS 로그인은 사용하지 않습니다."""

import argparse
import csv
import io
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.cloud_photos import (
    CLOUD_CATALOG, MANIFEST_FIELDS, WEB_CATALOG, PhotoCatalogError, build_cloud_rows,
)
from scripts.upload_place_photos import atomic_text


def export_urls(root, cdn_base):
    root = Path(root)
    rows = build_cloud_rows(root / WEB_CATALOG, cdn_base)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_text(root / CLOUD_CATALOG, buffer.getvalue())
    return len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--cdn-base-url", required=True)
    args = parser.parse_args(argv)
    try:
        count = export_urls(args.root, args.cdn_base_url)
    except (OSError, PhotoCatalogError) as error:
        print(f"주소 목록 생성 실패: {error}")
        return 1
    print(f"사진 주소 생성: {count:,}개")
    print("출력: data/processed/place_photos_cloud.csv")
    print("AWS 접속 없이 주소를 생성했습니다. 원격 파일의 존재와 체크섬을 검증한 결과는 아닙니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
