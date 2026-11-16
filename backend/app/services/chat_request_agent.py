"""대화 Agent의 모델 통신과 도구 실행에 요청 제어를 연결합니다."""

from contextlib import contextmanager
from functools import wraps

from openai import DefaultHttpxClient

from .chat_request_control import (
    get_chat_request_control,
    use_chat_request_control,
)


def guard_chat_tool(function, control):
    if control is None:
        return function

    @wraps(function)
    def guarded(*args, **kwargs):
        with use_chat_request_control(control):
            try:
                return function(*args, **kwargs)
            finally:
                control.checkpoint()

    return guarded


@contextmanager
def chat_model_http_client():
    control = get_chat_request_control()
    if control is None:
        yield None
        return

    control.checkpoint()

    def before_request(request):
        remaining = control.timeout_seconds(120)
        timeouts = dict(request.extensions.get("timeout") or {})
        for phase in ("connect", "read", "write", "pool"):
            configured = timeouts.get(phase)
            timeouts[phase] = (
                remaining if configured is None
                else min(configured, remaining)
            )
        request.extensions["timeout"] = timeouts

    def after_response(response):
        control.checkpoint()

    with DefaultHttpxClient(
        timeout=120,
        event_hooks={
            "request": [before_request],
            "response": [after_response],
        },
    ) as client:
        yield client