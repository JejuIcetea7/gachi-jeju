"""대화 요청의 HTTP 대기, 시간 초과와 취소 API를 처리합니다."""

import asyncio
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from ..services.chat_request_control import ChatRequestStopped
from ..services.chat_request_runtime import (
    ChatRequestBusy, ChatRequestConflict, get_chat_request_runtime,
)
from . import chat
from .plan import ErrorResponse, build_error_response


router = APIRouter()


class CancelChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=36, max_length=36)


class CancelChatResponse(BaseModel):
    request_id: str
    state: Literal["cancelled", "timed_out", "completed"]


def request_control_error(error):
    if isinstance(error, ChatRequestStopped):
        if error.reason == "timed_out":
            return build_error_response(
                504, "request_timeout",
                "전체 답변 처리 시간이 초과되었습니다. 다시 시도해 주세요.",
            )
        if error.reason == "completed":
            return build_error_response(
                409, "request_finished", "이미 처리가 끝난 요청입니다.",
            )
        return build_error_response(
            409, "request_cancelled", "요청이 취소되었습니다.",
        )
    if isinstance(error, ChatRequestConflict):
        return build_error_response(
            409, "request_conflict", "같은 요청 식별자를 다시 사용할 수 없습니다.",
        )
    if isinstance(error, ChatRequestBusy):
        return build_error_response(
            503, "chat_busy", "처리 중인 요청이 많습니다. 잠시 후 다시 시도해 주세요.",
        )
    return build_error_response(
        400, "invalid_request", "요청 식별자는 소문자 UUID4 형식이어야 합니다.",
    )


async def wait_for_chat_request(request, handle):
    try:
        while True:
            state = handle.control.state
            if state in ("cancelled", "timed_out"):
                raise ChatRequestStopped(state)
            if handle.future.done():
                return handle.future.result()
            if await request.is_disconnected():
                handle.control.cancel()
            await asyncio.sleep(0.05)
    finally:
        handle.control.cancel()


@router.post(
    "/chat",
    response_model=chat.ChatResponse,
    operation_id="create_chat_chat_post",
    responses={
        400: {"model": ErrorResponse, "description": "입력 오류"},
        409: {"model": ErrorResponse, "description": "요청 취소 또는 식별자 충돌"},
        429: {"model": ErrorResponse, "description": "AI 호출 한도 초과"},
        500: {"model": ErrorResponse, "description": "내부 처리 오류"},
        502: {"model": ErrorResponse, "description": "AI 응답 또는 생성 일정 오류"},
        503: {"model": ErrorResponse, "description": "처리 중인 요청 초과 또는 AI 서버 오류"},
        504: {"model": ErrorResponse, "description": "요청 전체 또는 AI 시간 초과"},
    },
)
async def create_chat_endpoint(
    chat_request: chat.ChatRequest,
    request: Request,
    request_id: Annotated[str | None, Header(alias="X-Chat-Request-ID")] = None,
):
    runtime = get_chat_request_runtime()
    try:
        handle = runtime.start(
            request_id if request_id is not None else str(uuid4()),
            lambda: chat.create_chat(chat_request),
        )
    except (ChatRequestBusy, ChatRequestConflict, ChatRequestStopped, ValueError) as error:
        return request_control_error(error)
    try:
        return await wait_for_chat_request(request, handle)
    except ChatRequestStopped as error:
        return request_control_error(error)


@router.post(
    "/chat/cancel",
    response_model=CancelChatResponse,
    responses={
        400: {"model": ErrorResponse, "description": "입력 오류"},
        503: {"model": ErrorResponse, "description": "요청 기록 한도 초과"},
    },
)
async def cancel_chat_endpoint(cancel_request: CancelChatRequest):
    try:
        state = get_chat_request_runtime().cancel(cancel_request.request_id)
        return {"request_id": cancel_request.request_id, "state": state}
    except (ChatRequestBusy, ValueError) as error:
        return request_control_error(error)