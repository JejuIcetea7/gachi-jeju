"""PDF에 포함할 나눔고딕 글꼴과 라이선스를 최초 한 번 내려받습니다."""

import hashlib
from pathlib import Path
import ssl
from urllib.request import urlopen

import truststore

FONT_DIRECTORY = Path(__file__).resolve().parents[1] / "backend" / "app" / "assets" / "fonts"
SOURCE = "https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothic/"
FILES = {
    "NanumGothic-Regular.ttf": "76f45ef4a6bcff344c837c95a7dcc26e017e38b5846d5ae0cdcb5b86be2e2d31",
    "NanumGothic-Bold.ttf": "f96298f9fb18e364d2370f4c3ce948ac67a2b61af992d7234bc15c42b033c674",
    "OFL.txt": None,
}


def validate_file(name, content):
    expected = FILES[name]
    if expected is not None:
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"{name}: 확인한 원본과 파일 내용이 다릅니다.")
    elif b"SIL OPEN FONT LICENSE Version 1.1" not in content:
        raise ValueError("나눔고딕 라이선스 파일을 확인하지 못했습니다.")


def prepare_fonts():
    pending = {}
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    for name in FILES:
        target = FONT_DIRECTORY / name
        if target.exists():
            validate_file(name, target.read_bytes())
            continue
        with urlopen(SOURCE + name, context=context, timeout=30) as response:
            content = response.read(3_000_001)
        if len(content) > 3_000_000:
            raise ValueError(f"{name}: 예상한 글꼴 파일 크기를 벗어났습니다.")
        validate_file(name, content)
        if name == "OFL.txt":
            content = b"\n".join(
                line.rstrip(b" \t") for line in content.splitlines()
            ) + b"\n"
        pending[name] = content

    FONT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for name, content in pending.items():
        target = FONT_DIRECTORY / name
        with target.open("xb") as output:
            output.write(content)
    print("PDF 한국어 글꼴과 라이선스 3개가 준비됐습니다.")


if __name__ == "__main__":
    prepare_fonts()
