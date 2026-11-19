"""대화 요청의 작업 수, 전체 제한 시간과 취소 식별자를 관리합니다."""

from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from uuid import UUID

from .chat_request_control import (
    CHAT_REQUEST_TIMEOUT_SECONDS, ChatRequestControl, ChatRequestStopped,
    use_chat_request_control,
)


REQUEST_ID_RETENTION_SECONDS = 300


class ChatRequestBusy(RuntimeError):
    pass


class ChatRequestConflict(RuntimeError):
    pass


def validate_chat_request_id(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("요청 식별자는 UUID4 형식이어야 합니다.")
    try:
        parsed = UUID(value)
    except ValueError:
        raise ValueError("요청 식별자는 UUID4 형식이어야 합니다.") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("요청 식별자는 소문자 UUID4 형식이어야 합니다.")
    return value


@dataclass(frozen=True)
class ChatRequestHandle:
    request_id: str
    control: ChatRequestControl
    future: Future


class ChatRequestRuntime:
    def __init__(
        self, max_workers=4, *, max_records=1024,
        timeout_seconds=CHAT_REQUEST_TIMEOUT_SECONDS, clock=monotonic,
    ):
        if type(max_workers) is not int or max_workers < 1:
            raise ValueError("동시 작업 수는 양의 정수여야 합니다.")
        if type(max_records) is not int or max_records < max_workers:
            raise ValueError("기록 수는 동시 작업 수 이상이어야 합니다.")
        ChatRequestControl(timeout_seconds, clock=clock)
        self._max_workers = max_workers
        self._max_records = max_records
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._lock = Lock()
        self._active = {}
        self._recent = {}
        self._closed = False
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="chat-request",
        )

    def _purge_locked(self):
        now = self._clock()
        for request_id, (expires_at, _) in list(self._recent.items()):
            if expires_at <= now:
                del self._recent[request_id]

    @staticmethod
    def _run(control, function):
        with use_chat_request_control(control):
            try:
                result = function()
            except Exception:
                control.checkpoint()
                control.complete()
                raise
            if not control.complete():
                control.checkpoint()
            return result

    def _finish(self, request_id, control):
        with self._lock:
            if self._active.get(request_id) is control:
                del self._active[request_id]
                self._recent[request_id] = (
                    self._clock() + REQUEST_ID_RETENTION_SECONDS, control.state,
                )

    def start(self, request_id, function):
        request_id = validate_chat_request_id(request_id)
        if not callable(function):
            raise ValueError("실행할 대화 처리 함수가 필요합니다.")
        with self._lock:
            self._purge_locked()
            if self._closed:
                raise ChatRequestBusy()
            if request_id in self._recent:
                if self._recent[request_id][1] == "cancelled":
                    raise ChatRequestStopped("cancelled")
                raise ChatRequestConflict()
            if request_id in self._active:
                raise ChatRequestConflict()
            if (
                len(self._active) >= self._max_workers
                or len(self._active) + len(self._recent) >= self._max_records
            ):
                raise ChatRequestBusy()
            control = ChatRequestControl(
                self._timeout_seconds, clock=self._clock,
            )
            self._active[request_id] = control
            context = copy_context()
            try:
                future = self._executor.submit(context.run, self._run, control, function)
            except Exception:
                del self._active[request_id]
                raise
        future.add_done_callback(lambda done: self._finish(request_id, control))
        return ChatRequestHandle(request_id, control, future)

    def cancel(self, request_id):
        request_id = validate_chat_request_id(request_id)
        with self._lock:
            self._purge_locked()
            control = self._active.get(request_id)
            if control is not None:
                control.cancel()
                return control.state
            if request_id in self._recent:
                return self._recent[request_id][1]
            if self._closed or len(self._active) + len(self._recent) >= self._max_records:
                raise ChatRequestBusy()
            self._recent[request_id] = (
                self._clock() + REQUEST_ID_RETENTION_SECONDS, "cancelled",
            )
            return "cancelled"

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            controls = list(self._active.values())
        for control in controls:
            control.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)


_runtime = None
_runtime_lock = Lock()


def get_chat_request_runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = ChatRequestRuntime()
        return _runtime


def shutdown_chat_request_runtime():
    global _runtime
    with _runtime_lock:
        runtime = _runtime
        _runtime = None
    if runtime is not None:
        runtime.close()