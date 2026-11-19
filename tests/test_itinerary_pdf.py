import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from pypdf import PdfReader

from backend.app.main import app
from backend.app.routers import itinerary_pdf as pdf_router
from backend.app.services import itinerary_pdf as pdf_service
from PIL import Image
from backend.app.routers import place_images as images_router

def item(kind="visit", **changes):
    return {
        "start_time": "09:00", "end_time": "10:00", "item_type": kind,
        "title": "한라수목원", "place_id": "P044" if kind == "visit" else None,
        "verified": True if kind == "visit" else None, "category": None,
        "latitude": None, "longitude": None, "transport": None,
        "duration_minutes": 60, "distance_km": None,
        "accessibility_notes": ["전체 휠체어 동선은 확인되지 않았습니다."],
        "route_id": None, **changes,
    }


def snapshot():
    return {
        "options": {
            "departure": "제주빅데이터센터", "start_date": "2026-11-20",
            "trip_days": 3, "transport": "driving", "mobility_aid": "wheelchair",
            "return_to_origin": True, "requirements": ["accessible_toilet"],
            "lodging_status": "undecided", "lodging_stays": [{
                "night": 1, "name": "선택한 제주 숙소", "address": "제주 숙소 주소",
                "latitude": 33.25, "longitude": 126.4,
            }],
        },
        "plan": {
            "assumptions": ["현장 운영 여부는 방문 전에 확인해 주세요."],
            "days": [
                {"day": 1, "items": [item()]},
                {"day": 2, "items": [item("move", title="선택한 제주 숙소 → 센터", transport="자동차", distance_km=1.25)]},
                {"day": 3, "items": []},
            ],
        },
        "warnings": ["확인되지 않은 시설 조건이 있습니다."],
        "transit_routes": [],
    }


def render(value):
    request = pdf_router.ItineraryPdfRequest.model_validate(value)
    return pdf_service.build_itinerary_pdf(request.model_dump(mode="json"))


def pdf_text(content):
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(content)).pages)

def representative_jpeg():
    output = BytesIO()
    Image.new("RGB", (640, 400), "orange").save(output, "JPEG")
    return output.getvalue()

class ItineraryPdfTests(unittest.TestCase):
    def setUp(self):
        self.photos = self.enterContext(patch.object(
            pdf_service, "get_representative_image", return_value=None,
        ))
    def test_all_days_lodging_notes_and_korean_font_are_preserved(self):
        value = snapshot()
        before = copy.deepcopy(value)
        content = render(value)
        text = pdf_text(content)
        for phrase in ("한라수목원", "선택한 제주 숙소", "제주 숙소 주소", "1일차", "2일차", "3일차",
                       "2026-11-22", "전체 휠체어 동선", "확인되지 않은 시설", "현장 운영 여부",
                       "저장된 방문·이동 일정이 없습니다."):
            self.assertIn(phrase, text)
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertIn(b"/FontFile2", content)
        self.assertEqual(value, before)

    def test_markup_is_literal_and_never_fetches_external_content(self):
        value = snapshot()
        value["plan"]["days"][0]["items"][0]["title"] = '<img src="https://example.invalid/a"> & 관광지'
        text = pdf_text(render(value))
        self.assertIn('<img src="https://example.invalid/a"> & 관광지', text)

    def test_long_accessibility_notes_continue_without_losing_entries(self):
        value = snapshot()
        notes = [f"검증 메모 {index}: " + "계단과 경사를 현장에서 확인해 주세요. " * 15 for index in range(18)]
        value["plan"]["days"][0]["items"][0]["accessibility_notes"] = notes
        content = render(value)
        reader = PdfReader(BytesIO(content))
        text = pdf_text(content)
        self.assertGreater(len(reader.pages), 3)
        for index in range(18):
            self.assertIn(f"검증 메모 {index}:", text)

    def test_transit_stops_and_unknown_vehicle_information_are_not_guessed(self):
        value = snapshot()
        value["plan"]["days"][1]["items"][0].update(transport="대중교통", route_id="bus-1")
        value["transit_routes"] = [{
            "route_id": "bus-1", "requested_at": "2026-11-18T00:00:00+00:00",
            "segments": [{"type": "BUS", "route_numbers": ["281"], "boarding_stop": "출발 정류장",
                          "alighting_stop": "도착 정류장", "ride_stop_count": None}],
        }]
        text = pdf_text(render(value))
        for phrase in ("281", "출발 정류장", "도착 정류장", "정거장 수 미확인", "2026-11-18 09:00"):
            self.assertIn(phrase, text)
        self.assertNotIn("저상버스", text)

    def test_duplicate_transit_evidence_is_not_selected(self):
        value = snapshot()
        value["plan"]["days"][1]["items"][0].update(transport="버스", route_id="duplicate")
        record = {"route_id": "duplicate", "requested_at": "2026-11-18T00:00:00Z",
                  "segments": [{"type": "BUS", "route_numbers": ["999"]}]}
        value["transit_routes"] = [record, copy.deepcopy(record)]
        text = pdf_text(render(value))
        self.assertIn("버스 승하차 상세 정보 미확인", text)
        self.assertNotIn("999", text)

    def test_missing_dates_and_unrequested_return_remain_explicit(self):
        value = snapshot()
        value["options"].update(start_date=None, return_to_origin=False)
        text = pdf_text(render(value))
        self.assertIn("날짜 미정", text)
        self.assertIn("요청하지 않음", text)

    def test_duplicate_out_of_range_and_overflowing_days_are_rejected(self):
        for numbers, options in (
            ([1, 1], {}), ([2, 1], {}), ([0], {}), ([4], {}),
            ([367], {"trip_days": 367}),
            ([2], {"start_date": "9999-12-31", "trip_days": None, "lodging_stays": []}),
        ):
            with self.subTest(numbers=numbers, options=options):
                value = snapshot()
                value["options"].update(options)
                value["plan"]["days"] = [{"day": day, "items": []} for day in numbers]
                with self.assertRaises(ValidationError):
                    pdf_router.ItineraryPdfRequest.model_validate(value)

    def test_large_text_and_nonfinite_numbers_are_rejected(self):
        for changes in ({"title": "가" * 4001}, {"distance_km": float("nan")}):
            with self.subTest(changes=changes):
                value = snapshot()
                value["plan"]["days"][0]["items"][0].update(changes)
                with self.assertRaises(ValidationError):
                    pdf_router.ItineraryPdfRequest.model_validate(value)

    def test_extra_chat_history_and_map_geometry_are_rejected(self):
        for key in ("messages", "map_routes"):
            with self.subTest(key=key):
                value = snapshot()
                value[key] = []
                with self.assertRaises(ValidationError):
                    pdf_router.ItineraryPdfRequest.model_validate(value)

    def test_missing_fonts_have_a_clear_error(self):
        with (
            patch.object(pdf_service, "FONT_NAMES", ("GachiMissingRegular", "GachiMissingBold")),
            patch.object(pdf_service, "FONT_DIRECTORY", Path(__file__).parent / "missing-pdf-fonts"),
        ):
            with self.assertRaises(pdf_service.PdfUnavailableError):
                render(snapshot())

    def test_parallel_downloads_do_not_mix_document_text(self):
        values = [snapshot(), snapshot()]
        values[0]["plan"]["days"][0]["items"][0]["title"] = "첫 번째 독립 여행"
        values[1]["plan"]["days"][0]["items"][0]["title"] = "두 번째 독립 여행"
        with ThreadPoolExecutor(max_workers=2) as executor:
            texts = list(executor.map(lambda value: pdf_text(render(value)), values))
        self.assertIn("첫 번째 독립 여행", texts[0])
        self.assertNotIn("두 번째 독립 여행", texts[0])
        self.assertIn("두 번째 독립 여행", texts[1])
        self.assertNotIn("첫 번째 독립 여행", texts[1])

    def test_saved_time_uses_korea_timezone(self):
        request = pdf_router.ItineraryPdfRequest.model_validate(snapshot())
        content = pdf_service.build_itinerary_pdf(
            request.model_dump(mode="json"),
            created_at=datetime(2026, 11, 18, 0, 30, tzinfo=timezone.utc),
        )
        self.assertIn("2026-11-18 09:30", pdf_text(content))

    def test_same_photo_is_loaded_once_and_only_used_for_verified_visits(self):
        self.photos.return_value = representative_jpeg()
        value = snapshot()
        value["plan"]["days"][0]["items"] += [
            item("move", place_id="P044"),
            item(verified=False, place_id="P044"),
            item(place_id="P001", verified=False),
        ]
        value["plan"]["days"][2]["items"] = [item()]

        pdf = render(value)
        pages = PdfReader(BytesIO(pdf)).pages
        self.assertEqual([len(page.images) for page in pages], [1, 0, 1])
        self.assertEqual(pages[0].images[0].image.size, (640, 400))
        self.assertIn("여행지 정보 확인 필요", pdf_text(pdf))
        self.photos.assert_called_once()
        self.assertEqual(self.photos.call_args.args, ("P044",))


class ItineraryPdfHttpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.photos = self.enterContext(patch.object(
            pdf_service, "get_representative_image", return_value=None,
        ))
    async def test_http_download_headers_and_full_pdf(self):
        self.photos.return_value = representative_jpeg()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/itineraries/pdf", json=snapshot())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("filename*=UTF-8''", response.headers["content-disposition"])
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertIn("3일차", pdf_text(response.content))
        self.assertEqual(len(PdfReader(BytesIO(response.content)).pages[0].images), 1)

    async def test_invalid_request_returns_existing_error_format(self):
        value = snapshot()
        value["plan"]["days"] = []
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/itineraries/pdf", json=value)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")

    async def test_pdf_failure_does_not_break_health_or_return_fake_pdf(self):
        with patch.object(pdf_router, "build_itinerary_pdf",
                          side_effect=pdf_service.PdfUnavailableError("PDF 글꼴이 준비되지 않았습니다.")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/itineraries/pdf", json=snapshot())
                health = await client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "pdf_unavailable")
        self.assertEqual(health.status_code, 200)

    async def test_representative_endpoint_serves_jpeg_and_handles_missing_photo(self):
        content = representative_jpeg()
        with patch.object(
            images_router, "get_representative_image",
            side_effect=lambda place_id: content if place_id == "P044" else None,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test",
            ) as client:
                photo = await client.get("/places/P044/representative-image")
                missing = await client.get("/places/P002/representative-image")

        self.assertEqual(photo.status_code, 200)
        self.assertEqual(photo.headers["content-type"], "image/jpeg")
        self.assertEqual(photo.content, content)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
