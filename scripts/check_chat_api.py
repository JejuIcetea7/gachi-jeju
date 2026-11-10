import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    payload = {
        "message": (
            "부모님과 2박 3일 제주 여행을 하고 싶어요. "
            "여행 날짜와 이동수단은 아직 정하지 않았어요."
        ),
    }
    request = Request(
        "http://127.0.0.1:8000/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=180) as response:
            status = response.status
            data = json.load(response)
    except HTTPError as error:
        print("HTTP:", error.code)
        print(error.read().decode("utf-8", errors="replace"))
        return
    except URLError as error:
        print("백엔드에 연결하지 못했습니다:", error.reason)
        return
    except TimeoutError:
        print("응답 대기 시간이 180초를 넘었습니다.")
        return

    print("HTTP:", status)
    print("Agent 답변:", data.get("reply"))
    print(
        "정리된 옵션:",
        json.dumps(data.get("options"), ensure_ascii=False, indent=2),
    )
    print("일정 포함:", data.get("plan") is not None)
    print("실행 정보:", data.get("meta"))


if __name__ == "__main__":
    main()