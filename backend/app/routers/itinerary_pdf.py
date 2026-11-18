"""화면에 표시된 여행 일정의 PDF 다운로드 API입니다."""

from datetime import date
import math
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .chat import ChatOptions
from .plan import PlanBodyResponse, build_error_response
from ..services.itinerary_pdf import build_itinerary_pdf, PdfUnavailableError
from ..services.transit_guidance import TransitRouteGuidance

router = APIRouter()
PdfText = Annotated[str, StringConstraints(max_length=4000)]


class ItineraryPdfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    options: ChatOptions
    plan: PlanBodyResponse
    warnings: list[PdfText] = Field(default_factory=list, max_length=100)
    transit_routes: list[TransitRouteGuidance] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def validate_export_size_and_days(self):
        numbers = [day.day for day in self.plan.days]
        if not numbers or numbers != sorted(set(numbers)) or numbers[0] < 1:
            raise ValueError("일차는 중복 없이 순서대로 보내 주세요.")
        if numbers[-1] > 366 or (self.options.trip_days is not None and numbers[-1] > self.options.trip_days):
            raise ValueError("PDF로 저장할 여행 일차의 범위를 확인해 주세요.")
        if self.options.start_date is not None and numbers[-1] > (date.max - self.options.start_date).days + 1:
            raise ValueError("PDF에 표시할 날짜가 지원 범위를 벗어났습니다.")
        if sum(len(day.items) for day in self.plan.days) > 2000:
            raise ValueError("PDF 한 번에 저장할 일정 항목은 2,000개 이하입니다.")
        total = 0
        stack = [self.model_dump(mode="json")]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("PDF에 넣을 숫자는 유한한 값이어야 합니다.")
            elif isinstance(value, str):
                if len(value) > 4000:
                    raise ValueError("PDF에 넣을 문장은 각각 4,000자 이하로 보내 주세요.")
                total += len(value)
        if total > 300_000:
            raise ValueError("PDF에 넣을 전체 문장의 길이를 줄여 주세요.")
        return self


@router.post("/itineraries/pdf", response_class=Response)
def download_itinerary_pdf(request: ItineraryPdfRequest):
    try:
        content = build_itinerary_pdf(request.model_dump(mode="json"))
    except PdfUnavailableError as error:
        return build_error_response(503, "pdf_unavailable", str(error))

    day = request.options.start_date.isoformat() if request.options.start_date else "날짜미정"
    filename = quote(f"같이가는제주_{day}_여행일정.pdf")
    return Response(content=content, media_type="application/pdf", headers={
        "Content-Disposition": "attachment; filename=\"itinerary.pdf\"; filename*=UTF-8''" + filename,
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    })
