"""평가 전용 프로세스에서 실제 POST /chat을 호출하고 시도별 근거를 기록합니다."""
from copy import deepcopy
from datetime import date
import json
from threading import Lock
from time import perf_counter
from unittest.mock import patch


_CAPTURE_LOCK = Lock()
_ROUTE_FIELDS = (
    "route_id", "transport", "origin", "destination",
    "distance_m", "duration_seconds", "geometry_status", "requested_at",
)


def _capture_result(result):
    from backend.app.services.plan_evaluation import (
        calculate_process_metrics, extract_tool_trace,
    )
    from backend.app.services.plan_retry import merge_agent_metrics

    messages = result.get("messages")
    trace = extract_tool_trace(messages or [])
    reply = result.get("chat_reply")
    if hasattr(reply, "model_dump"):
        reply = reply.model_dump(mode="json")
    captured = deepcopy({
        "chat_reply": reply,
        "tool_trace": trace,
        "route_calls": [
            {key: route[key] for key in _ROUTE_FIELDS if key in route}
            for route in (result.get("route_calls") or [])
        ],
        "metrics": merge_agent_metrics([result]),
        "process": (
            calculate_process_metrics(messages, trace)
            if messages is not None else None
        ),
    })
    json.dumps(captured, ensure_ascii=False, allow_nan=False)
    return captured


def run_chat_request(request_body, *, agent_runner, reference_date=None):
    """실제 라우터를 순차 실행합니다. 외부 호출 여부는 전달한 agent_runner가 결정합니다.

    기존 개발 서버에 접속하지 않고 동일한 FastAPI 앱을 프로세스 안에서 실행합니다.
    관찰 실패는 capture_error에 남기며 실제 서버 응답을 변경하지 않습니다.
    경로 기록에서는 도로선 좌표를 제외하고, 예외는 유형만 기록합니다.
    """
    if not isinstance(request_body, dict):
        raise ValueError("평가 요청은 객체여야 합니다.")
    if not callable(agent_runner):
        raise ValueError("평가에 사용할 Agent 실행 함수가 필요합니다.")
    if reference_date is not None and type(reference_date) is not date:
        raise ValueError("기준 날짜는 datetime.date 형식이어야 합니다.")

    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app.routers import chat as chat_router

    saved_request = deepcopy(request_body)
    attempts = []

    def observe_agent(messages, response_format):
        attempt = {
            "attempt": len(attempts) + 1,
            "status": "running",
            "error_type": None,
            "capture_error": None,
            "retry_feedback": [],
        }
        attempts.append(attempt)
        try:
            payload = json.loads(messages[-1]["content"])
            attempt["retry_feedback"] = deepcopy(
                payload.get("plan_retry", {}).get("validation_errors", [])
            )
        except (IndexError, KeyError, TypeError, ValueError, AttributeError):
            attempt["capture_error"] = "invalid_retry_payload"

        try:
            result = agent_runner(messages, response_format)
        except Exception as error:
            attempt["status"] = "error"
            attempt["error_type"] = type(error).__name__
            raise

        attempt["status"] = "returned"
        try:
            attempt.update(_capture_result(result))
        except Exception as error:
            attempt["capture_error"] = type(error).__name__
        return result

    started_at = perf_counter()
    # 모듈 함수 교체가 겹치지 않도록 이 평가 함수의 호출은 직렬화합니다.
    with _CAPTURE_LOCK:
        original_builder = chat_router.build_chat_messages

        def build_messages(chat_request):
            return original_builder(chat_request, today=reference_date)

        with (
            patch.object(chat_router, "run_chat_agent", observe_agent),
            patch.object(chat_router, "build_chat_messages", build_messages),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            response = client.post("/chat", json=deepcopy(saved_request))
            body = response.json()

    return {
        "request": saved_request,
        "reference_date": (
            reference_date.isoformat() if reference_date is not None else None
        ),
        "http_status": response.status_code,
        "response": body,
        "attempts": deepcopy(attempts),
        "elapsed_seconds": round(perf_counter() - started_at, 3),
    }