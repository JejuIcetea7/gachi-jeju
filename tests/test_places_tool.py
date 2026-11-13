import unittest

from backend.app.tools.places import (
    calculate_distance_km,
    get_place_details,
    search_places,
)

# 제주국제공항 좌표
CENTER = {"center_latitude": 33.5068, "center_longitude": 126.4927}

class DistanceTests(unittest.TestCase):
    def test_same_point_is_zero(self):
        distance_km = calculate_distance_km(33.5, 126.5, 33.5, 126.5)

        self.assertEqual(distance_km, 0.0)

    def test_airport_to_seogwipo_city_hall(self):
        distance_km = calculate_distance_km(33.5068, 126.4927, 33.2541, 126.5600)

        self.assertAlmostEqual(distance_km, 28.7, delta=0.5)

class SearchPlacesTests(unittest.TestCase):
    def test_results_are_sorted_and_inside_radius(self):
        result = search_places(**CENTER)

        self.assertEqual(result["status"], "ok")
        places = result["places"]
        self.assertGreater(len(places), 0)
        self.assertLessEqual(len(places), 10)

        previous_distance_km = 0.0
        for place in places:
            self.assertLessEqual(place["distance_km"], 15.0)
            self.assertGreaterEqual(place["distance_km"], previous_distance_km)
            previous_distance_km = place["distance_km"]

    def test_category_filter(self):
        result = search_places(**CENTER, radius_km=100.0, category="해변")

        for place in result["places"]:
            self.assertEqual(place["category"], "해변")

    def test_max_count(self):
        result = search_places(**CENTER, radius_km=100.0, max_count=3)

        self.assertEqual(len(result["places"]), 3)

    def test_not_found(self):
        result = search_places(**CENTER, category="없는카테고리")

        self.assertEqual(result, {"status": "not_found", "places": []})

class PlaceDetailsTests(unittest.TestCase):
    def test_spots_have_no_empty_or_duplicate_description(self):
        first_place = search_places(**CENTER)["places"][0]

        result = get_place_details(place_id=first_place["place_id"])

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["name"], first_place["name"])
        seen_spots = set()
        for spot in result["spots"]:
            self.assertTrue(spot["description"])
            spot_key = (spot["name"], spot["description"])
            self.assertNotIn(spot_key, seen_spots)
            seen_spots.add(spot_key)

    def test_not_found(self):
        result = get_place_details(place_id="없는ID")

        self.assertEqual(result, {"status": "not_found"})

    def test_closed_days_without_visit_dates(self):
        # P092 국립제주박물관: 매주 월요일 휴관
        result = get_place_details(place_id="P092")

        self.assertIn("매주 월요일", result["closed_days"])
        self.assertNotIn("closed_day_checks", result)

    def test_closed_day_checks_by_date(self):
        # 2026-12-14 월요일, 2026-12-15 화요일
        result = get_place_details(
            place_id="P092",
            visit_dates=["2026-12-14", "2026-12-15", "12월 16일"],
        )

        self.assertEqual(result["closed_day_checks"], {
            "2026-12-14": "휴무",
            "2026-12-15": "영업",
            "12월 16일": "날짜 형식 오류",
        })

    def test_accessibility_facts_from_survey(self):
        # P166 퍼시픽마리나요트공원: 일반정보 HWP의 장애인 주차 5대, 화장실 내부 출입문 폭 94cm
        result = get_place_details(place_id="P166")

        facts = result["accessibility_facts"]
        fact_pairs = set()
        for fact in facts:
            self.assertNotIn(fact["raw_value"].strip(), ("", "-"))
            fact_pairs.add((fact["description"], fact["raw_value"]))
        self.assertIn(("장애인 주차 구역 주차면수", "5대"), fact_pairs)
        self.assertIn(("내부 출입문 폭", "94cm"), fact_pairs)

    def test_accessibility_facts_empty_without_survey(self):
        # P001 한림공원: 조사 문서(HWP) 없음
        result = get_place_details(place_id="P001")

        self.assertEqual(result["accessibility_facts"], [])

    def test_blank_closed_days_is_unknown(self):
        # P012 생각하는정원: 휴무일 확인필요(빈칸)
        result = get_place_details(place_id="P012", visit_dates=["2026-12-14"])

        self.assertEqual(result["closed_days"], "확인 안 됨")
        self.assertEqual(result["closed_day_checks"], {"2026-12-14": "확인 안 됨"})

    def test_opening_hours_and_admission_fee(self):
        # P042 한라생태숲: 계절별 운영시간, P040 돌문화공원: 관광공사 API 관람료
        result = get_place_details(place_id="P042")

        self.assertEqual(
            result["opening_hours"],
            "3-10월 09:00-18:00 입장마감 17:00;11-2월 09:00-17:00 입장마감 16:00",
        )
        self.assertIn("하절기", result["opening_hours_raw"])
        self.assertIn("어른 5,000원", get_place_details(place_id="P040")["admission_fee"])

    def test_blank_opening_hours_is_unknown(self):
        # P100 동문시장: 운영시간 원문이 '점포별로 상이함'이라 정리 값 없음
        result = get_place_details(place_id="P100")

        self.assertEqual(result["opening_hours"], "확인 안 됨")
        self.assertEqual(result["opening_hours_raw"], "점포별로 상이함")

if __name__ == "__main__":
    unittest.main()
