"""대화 요청의 전체 시간 예산과 취소 상태를 요청별로 관리합니다."""

from contextlib import contextmanager
from contextvars import ContextVar
import math
from threading import Lock
from time import monotonic


CHAT_REQUEST_TIMEOUT_SECONDS = 150


def _finite_number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label}은 유한한 숫자여야 합니다.")
    return float(value)


def _positive_seconds(value):
    value = _finite_number(value, "제한 시간")
    if value <= 0:
        raise ValueError("제한 시간은 0보다 커야 합니다.")
    return value


class ChatRequestStopped(RuntimeError):
    MESSAGES = {
        "cancelled": "요청이 취소되었습니다.",
        "timed_out": "요청 처리 제한 시간을 초과했습니다.",
        "completed": "이미 처리가 끝난 요청입니다.",
    }

    def __init__(self, reason):
        if reason not in self.MESSAGES:
            raise ValueError("지원하지 않는 요청 중단 사유입니다.")
        self.reason = reason
        super().__init__(self.MESSAGES[reason])


class ChatRequestControl:
    """여러 생성 시도에서 같은 객체를 공유합니다. 진행 중인 통신을 강제 종료하지 않습니다."""

    def __init__(self, timeout_seconds=CHAT_REQUEST_TIMEOUT_SECONDS, *, clock=monotonic):
        duration = _positive_seconds(timeout_seconds)
        if not callable(clock):
            raise ValueError("시계는 호출 가능한 함수여야 합니다.")
        self._clock = clock
        self._deadline = self._now() + duration
        if not math.isfinite(self._deadline):
            raise ValueError("요청 종료 시각의 범위를 벗어났습니다.")
        self._state = "active"
        self._lock = Lock()

    def _now(self):
        return _finite_number(self._clock(), "시계 값")

    def _remaining_locked(self):
        if self._state != "active":
            return 0.0
        remaining = self._deadline - self._now()
        if remaining <= 0:
            self._state = "timed_out"
            return 0.0
        return remaining

    @property
    def deadline(self):
        return self._deadline

    @property
    def state(self):
        with self._lock:
            self._remaining_locked()
            return self._state

    def remaining_seconds(self):
        with self._lock:
            return self._remaining_locked()

    def checkpoint(self):
        with self._lock:
            self._remaining_locked()
            if self._state != "active":
                raise ChatRequestStopped(self._state)

    def timeout_seconds(self, maximum):
        maximum = _positive_seconds(maximum)
        with self._lock:
            remaining = self._remaining_locked()
            if self._state != "active":
                raise ChatRequestStopped(self._state)
            return min(maximum, remaining)

    def cancel(self):
        with self._lock:
            self._remaining_locked()
            if self._state != "active":
                return False
            self._state = "cancelled"
            return True

    def complete(self):
        with self._lock:
            self._remaining_locked()
            if self._state == "completed":
                return True
            if self._state != "active":
                return False
            self._state = "completed"
            return True


_CURRENT_CONTROL = ContextVar("gachi_jeju_chat_request_control", default=None)


def get_chat_request_control():
    return _CURRENT_CONTROL.get()


@contextmanager
def use_chat_request_control(control):
    if not isinstance(control, ChatRequestControl):
        raise ValueError("대화 요청 제어 객체가 필요합니다.")
    control.checkpoint()
    token = _CURRENT_CONTROL.set(control)
    try:
        yield control
    finally:
        _CURRENT_CONTROL.reset(token)


def check_chat_request_control():
    control = get_chat_request_control()
    if control is not None:
        control.checkpoint()


def get_chat_request_timeout(maximum):
    _positive_seconds(maximum)
    control = get_chat_request_control()
    return maximum if control is None else control.timeout_seconds(maximum)