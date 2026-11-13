import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from .routers import chat, lodgings, place_details, place_images, plan
from .routers.plan import build_error_response

ALLOWED_ORIGINS = ["http://localhost:3000", "http://localhost:5173"]

logger = logging.getLogger(__name__)

app = FastAPI(title="같이 가는 제주 API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(plan.router)
app.include_router(chat.router)
app.include_router(place_images.router)
app.include_router(place_details.router)
app.include_router(lodgings.router)

def translate_validation_error(error: dict) -> str:
    """FastAPI 입력 오류를 화면에 보여 줄 한국어 이유로 바꿉니다."""
    error_type = error["type"]
    context = error.get("ctx") or {}

    reason = error["msg"]
    if error_type == "missing":
        reason = "필수 항목입니다."
    elif error_type == "string_too_short":
        reason = "비어 있을 수 없습니다."
    elif error_type == "string_too_long":
        reason = f"{context.get('max_length')}자 이하로 입력해 주세요."
    elif error_type == "literal_error":
        reason = f"선택할 수 없는 값입니다. 가능한 값: {context.get('expected')}"
    elif error_type.startswith("date"):
        reason = "날짜 형식(YYYY-MM-DD)이 아닙니다."
    elif error_type in ("greater_than_equal", "less_than_equal"):
        reason = "허용 범위를 벗어났습니다."
    elif error_type == "too_short":
        reason = "1개 이상 선택해 주세요."
    elif error_type in ("int_parsing", "int_type", "string_type", "list_type"):
        reason = "형식이 맞지 않습니다."
    return reason

@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exception: RequestValidationError):
    details = []
    for error in exception.errors():
        field_parts = []
        for location in error["loc"]:
            if location == "body":
                continue
            field_parts.append(str(location))

        field = ".".join(field_parts)
        if field == "":
            field = "body"

        detail = {"field": field, "reason": translate_validation_error(error)}
        details.append(detail)

    return build_error_response(400, "invalid_request", "입력값을 확인해 주세요.", details)

@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, exception: Exception):
    logger.exception("처리하지 못한 오류")
    return build_error_response(500, "internal_error", "서버 오류가 발생했습니다.")

@app.get("/health")
def check_health():
    return {"status": "ok"}
