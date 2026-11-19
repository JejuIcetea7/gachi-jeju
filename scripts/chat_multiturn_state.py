"""연속 대화 평가에서 이전 HTTP 결과를 다음 /chat 요청으로 연결합니다.

정상 응답의 options와 대화만 이어받으며, plan이 null이면 기존 일정을 유지합니다.
오류 응답은 대화에 추가하지 않고 요청 당시의 옵션과 기존 일정을 유지합니다.
이 모듈은 API를 호출하거나 파일을 저장하지 않습니다.
"""
from copy import deepcopy
import json


def build_followup_request(previous_record, message, *, options_patch=None):
    """화면의 대화 연결 규칙으로 다음 요청을 만들고 API 모델로 검사합니다.

    previous_record는 run_chat_request가 반환한 request/http_status/response를
    포함해야 합니다. options_patch는 다음 단계에서 명시한 선택 옵션만 덮어씁니다.
    자연어에서 조건을 추정하거나 이전 경로를 새로운 조회 근거로 복사하지 않습니다.
    """
    if (
        not isinstance(previous_record, dict)
        or not isinstance(previous_record.get("request"), dict)
        or not isinstance(previous_record.get("response"), dict)
    ):
        raise ValueError("이전 요청과 HTTP 응답 기록이 필요합니다.")
    status = previous_record.get("http_status")
    if type(status) is not int or (status != 200 and not 400 <= status <= 599):
        raise ValueError("이전 HTTP 상태는 200 또는 400~599여야 합니다.")
    if options_patch is not None and not isinstance(options_patch, dict):
        raise ValueError("선택 옵션 변경은 객체여야 합니다.")

    from backend.app.routers.chat import ChatRequest, ChatResponse
    from backend.app.routers.plan import ErrorResponse

    # 관찰 메타데이터가 아닌 실제 요청과 응답의 JSON 형식을 확인합니다.
    try:
        for payload in (previous_record["request"], previous_record["response"], options_patch):
            json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("요청·응답·옵션은 유효한 JSON 값이어야 합니다.") from error

    request = ChatRequest.model_validate(
        deepcopy(previous_record["request"]),
    ).model_dump(mode="json")
    history = request["history"]
    options = request["options"]
    current_plan = request["current_plan"]

    if status == 200:
        response = ChatResponse.model_validate(
            deepcopy(previous_record["response"]),
        ).model_dump(mode="json")
        history.extend([
            {"role": "user", "content": request["message"]},
            {"role": "assistant", "content": response["reply"]},
        ])
        options = response["options"]
        if response["plan"] is not None:
            current_plan = response["plan"]
    else:
        ErrorResponse.model_validate(deepcopy(previous_record["response"]))

    return ChatRequest.model_validate({
        "message": message,
        "history": history[-20:],
        "options": {**options, **deepcopy(options_patch or {})},
        "current_plan": current_plan,
    }).model_dump(mode="json")
