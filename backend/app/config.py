import os
from pathlib import Path

LLM_MODEL = "gpt-6-luna"
LLM_REASONING_EFFORT = "low"

def read_env_value(name: str) -> str:
    """환경변수를 먼저 보고, 없으면 프로젝트 루트의 .env에서 값을 읽습니다."""
    value = os.getenv(name)
    if value:
        return value.strip()

    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return ""

    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            return value.strip()

    return ""

def get_kakao_api_key() -> str:
    return read_env_value("KAKAO_REST_API_KEY")

def get_openai_api_key() -> str:
    return read_env_value("OPENAI_API_KEY")
