"""외부 URL 요청에 대화 요청의 취소 상태와 남은 시간을 적용합니다."""

from contextlib import contextmanager
from urllib.error import HTTPError

from .chat_request_control import (
    ChatRequestStopped,
    check_chat_request_control,
    get_chat_request_timeout,
)


@contextmanager
def open_chat_url(opener, *args, timeout=20, **kwargs):
    request_timeout = get_chat_request_timeout(timeout)
    http_error = None
    try:
        with opener(*args, timeout=request_timeout, **kwargs) as response:
            check_chat_request_control()
            yield response
    except HTTPError as error:
        http_error = error
        raise
    finally:
        try:
            check_chat_request_control()
        except ChatRequestStopped:
            if http_error is not None:
                http_error.close()
            raise