"""여행 일정 PDF의 이메일 발송 요청 API입니다."""

from typing import Annotated, Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints, field_validator

from .itinerary_pdf import ItineraryPdfRequest
from .plan import build_error_response
from ..services.itinerary_email import (
    ItineraryEmailError,
    normalize_email_address,
)
from ..services.itinerary_email_delivery import deliver_itinerary_email

router = APIRouter()


class ItineraryEmailRequest(ItineraryPdfRequest):
    recipient: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=3,
            max_length=254,
        ),
    ]

    @field_validator("recipient", mode="before")
    @classmethod
    def validate_recipient(cls, value):
        return normalize_email_address(value)


class ItineraryEmailResponse(BaseModel):
    status: Literal["accepted"]
    duplicate: bool
    message: str


@router.post("/itineraries/email", response_model=ItineraryEmailResponse)
def send_itinerary_email(request: ItineraryEmailRequest):
    snapshot = request.model_dump(mode="json", exclude={"recipient"})
    try:
        duplicate = deliver_itinerary_email(snapshot, request.recipient)
    except ItineraryEmailError as error:
        response = build_error_response(
            error.status_code, error.code, str(error),
        )
    else:
        message = (
            "최근에 같은 일정을 메일 서버에 전달했습니다. "
            "수신함과 스팸함을 확인해 주세요."
            if duplicate else
            "메일 서버가 PDF 메일을 접수했습니다. "
            "수신함과 스팸함을 확인해 주세요."
        )
        body = ItineraryEmailResponse(
            status="accepted",
            duplicate=duplicate,
            message=message,
        )
        response = JSONResponse(body.model_dump())

    response.headers["Cache-Control"] = "private, no-store"
    return response