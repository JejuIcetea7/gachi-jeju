"""무장애여행정보 HWP를 kordoc로 md · json 으로 변환한다.

- 일반정보 HWP (공공데이터포털 D관광정보 49개) -> data/processed/hwp_general_info/<데이터ID>.md·json
- 허브 HWP (hub_102 ~ hub_121, 20개)            -> data/processed/hwp_hub_info/hub_<번호>.md·json

사진은 뽑지 않고(--no-images) 자리 표시(image_001.png 등)만 남긴다.
허브 HWP는 Git LFS 원본이라 실제 파일이 아니면(포인터) 실패로 남긴다.

실행: python scripts/convert_hwp_general_info.py
필요: Node.js 20 이상 (npx 로 kordoc 를 받아 실행)
"""

import csv
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIRECTORY = PROJECT_ROOT / "data" / "raw"
PORTAL_DIRECTORY = RAW_DIRECTORY / "data_go_kr"
HUB_DIRECTORY = RAW_DIRECTORY / "jeju_datahub"
LINKS_FILE = PORTAL_DIRECTORY / "links.csv"
PROCESSED_DIRECTORY = PROJECT_ROOT / "data" / "processed"
GENERAL_INFO_OUTPUT_DIRECTORY = PROCESSED_DIRECTORY / "hwp_general_info"
HUB_INFO_OUTPUT_DIRECTORY = PROCESSED_DIRECTORY / "hwp_hub_info"

KORDOC_PACKAGE = "kordoc@4.24.63"
GENERAL_INFO_GROUP = "D관광정보"
HUB_HWP_PATTERN = "제주특별자치도_무장애여행정보_*.hwp"
LFS_POINTER_MAX_BYTES = 1024


def load_general_info_data_ids():
    data_ids = []
    with open(LINKS_FILE, encoding="utf-8-sig", newline="") as links_file:
        for row in csv.DictReader(links_file):
            if row["group"] != GENERAL_INFO_GROUP:
                continue
            data_ids.append(row["data_id"])
    return data_ids


def list_general_info_jobs():
    """(출력 이름, hwp 파일 또는 None) 목록"""
    jobs = []
    for data_id in load_general_info_data_ids():
        hwp_file = None
        source_directory = PORTAL_DIRECTORY / f"dgk_{data_id}"
        hwp_files = sorted(source_directory.glob("*.hwp"))
        if len(hwp_files) > 0:
            hwp_file = hwp_files[0]
        jobs.append((data_id, hwp_file))
    return jobs


def list_hub_info_jobs():
    """(출력 이름, hwp 파일) 목록. 출력 이름은 허브 폴더 이름(hub_<번호>)"""
    jobs = []
    for hwp_file in sorted(HUB_DIRECTORY.glob("hub_*/" + HUB_HWP_PATTERN)):
        hub_folder_name = hwp_file.parent.name
        jobs.append((hub_folder_name, hwp_file))
    return jobs


def run_kordoc(hwp_file, output_file, format_options):
    command = ["npx", "-y", KORDOC_PACKAGE, str(hwp_file), "--no-images"]
    command.extend(format_options)
    command.extend(["-o", str(output_file)])

    error_message = None
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        error_message = str(error)
        return error_message

    if result.returncode != 0:
        error_message = result.stderr.strip()
    return error_message


def convert_hwp_file(hwp_file, output_directory, output_name):
    errors = []

    markdown_file = output_directory / f"{output_name}.md"
    markdown_options = ["--table-format", "gfm"]
    markdown_error = run_kordoc(hwp_file, markdown_file, markdown_options)
    if markdown_error is not None:
        errors.append(f"md: {markdown_error}")

    json_file = output_directory / f"{output_name}.json"
    json_options = ["--format", "json"]
    json_error = run_kordoc(hwp_file, json_file, json_options)
    if json_error is not None:
        errors.append(f"json: {json_error}")

    return errors


def convert_jobs(jobs, output_directory, label, failures):
    output_directory.mkdir(parents=True, exist_ok=True)
    converted_count = 0

    for output_name, hwp_file in jobs:
        if hwp_file is None:
            failures.append((output_name, "hwp 파일 없음"))
            continue
        if hwp_file.stat().st_size <= LFS_POINTER_MAX_BYTES:
            failures.append((output_name, "Git LFS 포인터 (git lfs fetch 필요)"))
            continue

        errors = convert_hwp_file(hwp_file, output_directory, output_name)
        if len(errors) > 0:
            failures.append((output_name, " / ".join(errors)))
            continue

        converted_count += 1
        print(f"[{label} {converted_count}/{len(jobs)}] {output_name} {hwp_file.name}")

    print(f"{label} 변환 완료: {converted_count}/{len(jobs)}")


def main():
    failures = []

    general_info_jobs = list_general_info_jobs()
    convert_jobs(general_info_jobs, GENERAL_INFO_OUTPUT_DIRECTORY, "일반정보", failures)

    hub_info_jobs = list_hub_info_jobs()
    convert_jobs(hub_info_jobs, HUB_INFO_OUTPUT_DIRECTORY, "허브", failures)

    for output_name, error_message in failures:
        print(f"실패: {output_name} {error_message}")


if __name__ == "__main__":
    main()
