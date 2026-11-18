"""현재 화면에 저장된 일정으로 한국어 PDF를 생성합니다."""

from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from threading import RLock
from xml.sax.saxutils import escape
from time import monotonic
from .place_representative_image import get_representative_image

FONT_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "fonts"
FONT_NAMES = ("GachiNanum", "GachiNanumBold")
FONT_LOCK = RLock()
KOREA = timezone(timedelta(hours=9))

TRANSPORT_LABELS = {"driving": "자동차", "walking": "도보", "transit": "대중교통"}
MOBILITY_LABELS = {"wheelchair": "휠체어", "stroller": "유아차", "none": "사용 안 함"}
REQUIREMENT_LABELS = {
    "step_free": "계단 피하기", "accessible_toilet": "장애인 화장실",
    "accessible_parking": "장애인 주차구역",
}


class PdfUnavailableError(RuntimeError):
    pass


def _register_fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont, TTFError

    with FONT_LOCK:
        for name, filename in zip(FONT_NAMES, ("NanumGothic-Regular.ttf", "NanumGothic-Bold.ttf")):
            if name in pdfmetrics.getRegisteredFontNames():
                continue
            path = FONT_DIRECTORY / filename
            if not path.is_file():
                raise PdfUnavailableError("PDF 한국어 글꼴이 준비되지 않았습니다.")
            try:
                pdfmetrics.registerFont(TTFont(name, str(path)))
            except (OSError, TTFError) as error:
                raise PdfUnavailableError("PDF 한국어 글꼴을 읽지 못했습니다.") from error


def _date_label(options, day=1):
    start = options.get("start_date")
    if not start:
        return "날짜 미정"
    return (date.fromisoformat(start) + timedelta(days=day - 1)).isoformat()


def _distance_label(km):
    if km is None:
        return "거리 미확인"
    return f"{km * 1000:,.0f}m" if km < 1 else f"{km:,.2f}km"


def _transit_lines(item, guidance):
    if item.get("item_type") != "move":
        return []
    mode = (item.get("transport") or "").replace(" ", "").lower()
    if mode not in {"transit", "bus", "버스", "대중교통", "대중교통(버스·도보)"}:
        return []
    if guidance is None:
        return ["버스 승하차 상세 정보 미확인"]
    lines = []
    for segment in guidance["segments"]:
        if segment["type"] == "BUS":
            numbers = " 또는 ".join(segment.get("route_numbers") or []) or "노선 미확인"
            boarding = segment.get("boarding_stop") or "승차 정류장 미확인"
            alighting = segment.get("alighting_stop") or "하차 정류장 미확인"
            count = segment.get("ride_stop_count")
            stops = f"{count}정거장 이동" if count is not None else "정거장 수 미확인"
            lines.append(f"버스 {numbers}: {boarding} 승차 → {alighting} 하차 / {stops}")
        elif segment["type"] == "WALKING":
            seconds = segment.get("duration_seconds")
            duration = f" / {(seconds + 59) // 60}분" if seconds is not None else ""
            lines.append("도보 연결" + duration)
        else:
            lines.append("연결 이동 구간 상세 정보 미확인")
    queried = datetime.fromisoformat(guidance["requested_at"].replace("Z", "+00:00"))
    lines.append("경로 조회 시각(한국): " + queried.astimezone(KOREA).strftime("%Y-%m-%d %H:%M"))
    lines.append("조회 시점의 경로입니다. 여행 당일 운행을 확인해 주세요.")
    return lines


def _load_visit_photos(plan):
    deadline = monotonic() + 8
    remaining_bytes = 4 * 1024 * 1024
    seen = set()
    photos = {}

    for day in plan["days"]:
        for item in day["items"]:
            place_id = item.get("place_id")
            if (
                item["item_type"] != "visit"
                or item.get("verified") is not True
                or not place_id
                or place_id in seen
            ):
                continue

            remaining_time = deadline - monotonic()
            if remaining_time <= 0 or remaining_bytes <= 0:
                return photos

            seen.add(place_id)
            content = get_representative_image(
                place_id, timeout=min(2.0, remaining_time),
            )
            if content is not None and len(content) <= remaining_bytes:
                photos[place_id] = content
                remaining_bytes -= len(content)

    return photos


def build_itinerary_pdf(snapshot, *, created_at=None):
    """등록된 대표 사진을 포함해 전달받은 전체 일정을 PDF로 반환합니다."""
    photos = _load_visit_photos(snapshot["plan"])
    with FONT_LOCK:
        return _render_itinerary_pdf(snapshot, created_at=created_at, photos=photos)


def _render_itinerary_pdf(snapshot, *, created_at, photos):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import (
            Image, LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle,
        )
        _register_fonts()
    except ImportError as error:
        raise PdfUnavailableError("PDF 생성 라이브러리가 설치되지 않았습니다.") from error

    options, plan = snapshot["options"], snapshot["plan"]
    green = colors.HexColor("#1f644f")
    ink = colors.HexColor("#173d35")
    styles = {
        "title": ParagraphStyle("PdfTitle", fontName=FONT_NAMES[1], fontSize=21, leading=29, textColor=green, spaceAfter=8),
        "heading": ParagraphStyle("PdfHeading", fontName=FONT_NAMES[1], fontSize=13, leading=20, textColor=ink, spaceBefore=12, spaceAfter=8, keepWithNext=True),
        "body": ParagraphStyle("PdfBody", fontName=FONT_NAMES[0], fontSize=9.5, leading=15, textColor=ink, wordWrap="CJK", spaceAfter=5),
        "small": ParagraphStyle("PdfSmall", fontName=FONT_NAMES[0], fontSize=8, leading=12, textColor=colors.HexColor("#52695d"), wordWrap="CJK", spaceAfter=4),
        "cell": ParagraphStyle("PdfCell", fontName=FONT_NAMES[0], fontSize=9, leading=14, textColor=ink, wordWrap="CJK"),
        "headcell": ParagraphStyle("PdfHeadCell", fontName=FONT_NAMES[1], fontSize=9, leading=14, textColor=ink, wordWrap="CJK"),
    }

    def paragraph(text, style="body"):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), styles[style])

    now = created_at or datetime.now(KOREA)
    if now.tzinfo is None:
        raise ValueError("PDF 저장 시각에는 시간대가 필요합니다.")
    now = now.astimezone(KOREA)
    output = BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=A4, leftMargin=42, rightMargin=42, topMargin=42,
        bottomMargin=45, title="같이 가는 제주 여행 일정", author="같이 가는 제주",
    )
    story = [paragraph("같이 가는 제주", "title"), paragraph("여행 일정표", "heading")]
    days = options.get("trip_days")
    period = _date_label(options)
    if days is not None and days > 1:
        period += " ~ " + _date_label(options, days)
    period += f" / {days}일" if days is not None else " / 기간 미정"
    summary = [
        "여행 기간: " + period,
        "출발지: " + (options.get("departure") or "미정"),
        "이동수단: " + TRANSPORT_LABELS.get(options.get("transport"), "미선택"),
        "이동 보조기구: " + MOBILITY_LABELS.get(options.get("mobility_aid"), "미선택"),
        "필수 이용 조건: " + (" / ".join(REQUIREMENT_LABELS.get(value, value) for value in options.get("requirements", [])) or "별도 지정 없음"),
        "출발지 귀환: " + {True: "요청함", False: "요청하지 않음", None: "미선택"}[options.get("return_to_origin")],
    ]
    story.extend(paragraph(value) for value in summary)
    story.append(paragraph("저장 시각(한국): " + now.strftime("%Y-%m-%d %H:%M"), "small"))

    route_counts, guidance_counts = {}, {}
    for day in plan["days"]:
        for item in day["items"]:
            if item.get("item_type") == "move" and item.get("route_id"):
                key = item["route_id"]
                route_counts[key] = route_counts.get(key, 0) + 1
    guidance = {}
    for record in snapshot.get("transit_routes", []):
        key = record["route_id"]
        guidance_counts[key] = guidance_counts.get(key, 0) + 1
        guidance[key] = record
    stays = {stay["night"]: stay for stay in options.get("lodging_stays", [])}

    def cell_parts(text, style):
        remaining = paragraph(text, style)
        while True:
            _, height = remaining.wrap(document.width - 190, 1_000_000)
            if height <= 240:
                yield remaining
                return
            parts = remaining.split(document.width - 190, 240)
            yield parts[0]
            remaining = parts[1]

    for index, day in enumerate(plan["days"]):
        if index:
            story.append(PageBreak())
        story.append(paragraph(f"{day['day']}일차 / {_date_label(options, day['day'])}", "heading"))
        previous_stay = stays.get(day["day"] - 1)
        stay = stays.get(day["day"])
        if previous_stay:
            story.append(paragraph("숙소 출발: " + previous_stay["name"]))
        if stay:
            story.append(paragraph("선택한 숙소: " + stay["name"]))
            if stay.get("address"):
                story.append(paragraph(stay["address"], "small"))
        elif days is not None and day["day"] < days and options.get("lodging_status") != "not_needed":
            story.append(paragraph("이 숙박일의 숙소는 미정입니다.", "small"))
        if not day["items"]:
            story.append(paragraph("이 날짜에는 저장된 방문·이동 일정이 없습니다."))
            continue

        rows = [[paragraph(value, "headcell") for value in ("시간", "일정과 확인사항", "소요시간")]]
        item_end_rows = []
        for item in day["items"]:
            kind = "방문" if item["item_type"] == "visit" else "이동"
            title_parts = list(cell_parts(f"[{kind}] {item['title']}", "cell"))
            details = [title_parts[0]]
            if item["item_type"] == "visit":
                details.append(paragraph("여행지 자료 등록" if item.get("verified") is True else "여행지 정보 확인 필요", "small"))
                photo = photos.get(item.get("place_id")) if item.get("verified") is True else None
                if photo is not None:
                    details.extend([
                        Spacer(1, 4),
                        Image(BytesIO(photo), width=144, height=90, hAlign="LEFT"),
                    ])
            key = item.get("route_id")
            selected = guidance.get(key) if route_counts.get(key) == 1 and guidance_counts.get(key) == 1 else None
            lines = _transit_lines(item, selected)
            minutes = item.get("duration_minutes")
            duration = f"{minutes}분" if minutes is not None else "시간 미확인"
            if item["item_type"] == "move":
                duration += "\n" + (item.get("transport") or "이동수단 미확인")
                duration += "\n" + _distance_label(item.get("distance_km"))
            rows.append([
                paragraph(f"{item['start_time']} ~ {item['end_time']}", "cell"),
                details, paragraph(duration, "cell"),
            ])
            for part in title_parts[1:]:
                rows.append(["", part, ""])
            for value in lines + ["- " + text for text in item.get("accessibility_notes", [])]:
                for part in cell_parts(value, "small"):
                    rows.append(["", part, ""])
            item_end_rows.append(len(rows) - 1)
        table = LongTable(rows, colWidths=[92, document.width - 174, 82], repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf4ee")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#d9e4da")),
            *[("LINEBELOW", (0, row), (-1, row), 0.5, colors.HexColor("#d9e4da")) for row in item_end_rows],
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ]))
        story.extend([table, Spacer(1, 10)])

    for heading, values in (
        ("확인할 사항", snapshot.get("warnings", [])),
        ("계획 전제와 확인사항", plan.get("assumptions", [])),
    ):
        if values:
            story.append(paragraph(heading, "heading"))
            story.extend(paragraph("- " + value) for value in values)
    story.append(Spacer(1, 10))
    story.append(paragraph("저장 당시의 일정과 안내입니다. 운영시간·교통 운행·접근 조건은 방문 전에 다시 확인해 주세요.", "small"))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#d9e4da"))
        canvas.line(42, 32, A4[0] - 42, 32)
        canvas.setFont(FONT_NAMES[0], 8)
        canvas.setFillColor(colors.HexColor("#52695d"))
        canvas.drawString(42, 20, "같이 가는 제주 / 여행 일정")
        canvas.drawRightString(A4[0] - 42, 20, f"{doc.page}쪽")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
