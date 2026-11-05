import os
from pathlib import Path

def get_kakao_api_key() -> str:
    key = os.getenv("KAKAO_REST_API_KEY")
    if key:
        return key.strip()

    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return ""

    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == "KAKAO_REST_API_KEY":
            return value.strip()

    return ""