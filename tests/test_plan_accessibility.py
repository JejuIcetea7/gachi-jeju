import copy
import unittest

from backend.app.services.plan_accessibility import (
    resolve_facility_status,
    validate_plan_accessibility,
)


def make_place(toilet="unknown", parking="unknown", facts=None):
    return {
        "place_id": "P001",
        "name": "DB 장소",
        "accessibility": {
            "accessible_toilet": toilet,
            "accessible_parking": parking,
            "has_slope_or_stairs": "Y",
        },
        "accessibility_facts": facts or [],
    }


def make_fact(value, description="장애인 화장실", section="기본정보"):
    return {
        "section": section,
        "description": description,
        "raw_value": value,
    }


def make_plan():
    return {"days": [{"day": 1, "items": [{
        "item_type": "visit",
        "place_id": "P001",
        "title": "AI 제목",
    }]}]}


class FacilityStatusTests(unittest.TestCase):
    def test_flags_preserve_known_and_unknown_values(self):
        for value, expected in [
            ("Y", "Y"), ("N", "N"), ("unknown", "unknown"),
            (None, "unknown"), (False, "unknown"), (0, "unknown"),
        ]:
            with self.subTest(value=value):
                place = make_place(value, value)
                for field in ("accessible_toilet", "accessible_parking"):
                    self.assertEqual(resolve_facility_status(place, field), expected)

    def test_explicit_survey_values_resolve_missing_flags(self):
        for field, description in [
            ("accessible_toilet", "장애인 화장실"),
            ("accessible_parking", "장애인 주차장 유무"),
            ("accessible_parking", "주차장 장애인 주차 유무"),
        ]:
            for value, expected in [
                ("있음", "Y"), ("유", "Y"), ("Y", "Y"),
                ("없음", "N"), ("무", "N"), ("N", "N"),
            ]:
                with self.subTest(field=field, value=value):
                    place = make_place(facts=[make_fact(value, description)])
                    self.assertEqual(resolve_facility_status(place, field), expected)

    def test_nearby_facility_and_other_sections_are_not_onsite_evidence(self):
        for fact in [
            make_fact("있음", "추천 장애인 화장실"),
            make_fact("있음", "주변(대체) 화장실"),
            make_fact("없음", section="추천 대상"),
        ]:
            with self.subTest(fact=fact):
                place = make_place(facts=[fact])
                self.assertEqual(
                    resolve_facility_status(place, "accessible_toilet"), "unknown"
                )

    def test_free_text_and_invalid_values_are_not_guessed(self):
        for value in ["주변에 있음", "없음 여부 미확인", "-", "", None, False, 0]:
            with self.subTest(value=value):
                place = make_place(facts=[make_fact(value)])
                self.assertEqual(
                    resolve_facility_status(place, "accessible_toilet"), "unknown"
                )

    def test_conflicting_sources_stay_unknown(self):
        for flag, raw in [("Y", "없음"), ("N", "있음")]:
            place = make_place(flag, facts=[make_fact(raw)])
            self.assertEqual(
                resolve_facility_status(place, "accessible_toilet"), "unknown"
            )
        for values in [("있음", "없음"), ("없음", "있음")]:
            place = make_place(facts=[make_fact(value) for value in values])
            self.assertEqual(
                resolve_facility_status(place, "accessible_toilet"), "unknown"
            )


class PlanAccessibilityTests(unittest.TestCase):
    def check_plan(self, place, requirements, mobility_aid=None):
        return validate_plan_accessibility(
            make_plan(),
            {"P001": place},
            requirements=requirements,
            mobility_aid=mobility_aid,
        )

    def test_confirmed_absence_rejects_selected_conditions(self):
        errors, warnings = self.check_plan(
            make_place("N", "N"), ["accessible_toilet", "accessible_parking"]
        )
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("장애인 화장실" in error for error in errors))
        self.assertTrue(any("장애인 주차구역" in error for error in errors))
        self.assertTrue(all("DB 장소" in error for error in errors))
        self.assertEqual(warnings, [])

    def test_known_presence_and_unselected_conditions_are_allowed(self):
        self.assertEqual(self.check_plan(
            make_place("Y", "Y"), ["accessible_toilet", "accessible_parking"]
        ), ([], []))
        self.assertEqual(self.check_plan(make_place("N", "N"), []), ([], []))

    def test_unknown_conditions_warn_without_claiming_absence(self):
        errors, warnings = self.check_plan(
            make_place(), ["accessible_toilet", "accessible_parking"]
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("장애인 화장실", warnings[0])
        self.assertIn("장애인 주차구역", warnings[0])
        self.assertIn("미확인", warnings[0])
        self.assertIn("보장된 일정이 아니므로", warnings[0])

    def test_combined_stair_slope_flag_never_confirms_a_visit_path(self):
        for flag in ("Y", "N", "unknown"):
            place = make_place("Y", "Y")
            place["accessibility"]["has_slope_or_stairs"] = flag
            errors, warnings = self.check_plan(place, ["step_free"])
            self.assertEqual(errors, [])
            self.assertEqual(len(warnings), 1)
            self.assertIn("계단 없는 방문 동선", warnings[0])

    def test_mobility_aid_does_not_imply_suitability_or_extra_requirements(self):
        for aid, label in [("wheelchair", "휠체어"), ("stroller", "유모차")]:
            errors, warnings = self.check_plan(make_place("N", "N"), [], aid)
            self.assertEqual(errors, [])
            self.assertEqual(len(warnings), 1)
            self.assertIn(label, warnings[0])
        self.assertEqual(self.check_plan(make_place(), [], "none"), ([], []))

    def test_missing_and_mismatched_place_records_stay_unverified(self):
        wrong = make_place("Y", "Y")
        wrong["place_id"] = "P002"
        for records in ({}, {"P001": None}, {"P001": wrong}):
            errors, warnings = validate_plan_accessibility(
                make_plan(), records, requirements=["accessible_toilet"]
            )
            self.assertEqual(errors, [])
            self.assertEqual(len(warnings), 1)

    def test_agent_claims_do_not_override_database_evidence(self):
        plan = make_plan()
        item = plan["days"][0]["items"][0]
        item["accessibility"] = {"accessible_toilet": "Y"}
        item["accessibility_notes"] = ["모든 시설 확인 완료"]
        errors, _ = validate_plan_accessibility(
            plan, {"P001": make_place("N")}, requirements=["accessible_toilet"]
        )
        self.assertEqual(len(errors), 1)

    def test_only_visits_are_checked_and_days_are_distinguished(self):
        plan = make_plan()
        plan["days"][0]["items"].insert(0, {"item_type": "move", "title": "이동"})
        plan["days"].append({"day": 2, "items": [{
            "item_type": "visit", "place_id": "P001", "title": "재방문",
        }]})
        plan["days"].append({"day": 3, "items": []})
        errors, warnings = validate_plan_accessibility(
            plan, {"P001": make_place()}, requirements=["accessible_toilet"]
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 2)
        self.assertIn("1일차", warnings[0])
        self.assertIn("2일차", warnings[1])

    def test_inputs_are_preserved_and_duplicate_conditions_are_not_repeated(self):
        plan = make_plan()
        records = {"P001": make_place("N")}
        requirements = ["accessible_toilet", "accessible_toilet", "step_free"]
        before = copy.deepcopy((plan, records, requirements))
        errors, warnings = validate_plan_accessibility(
            plan, records, requirements=requirements, mobility_aid="wheelchair"
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(warnings), 1)
        self.assertEqual((plan, records, requirements), before)

    def test_invalid_conditions_are_rejected(self):
        with self.assertRaises(ValueError):
            self.check_plan(make_place(), ["unsupported"])
        with self.assertRaises(ValueError):
            self.check_plan(make_place(), [], "unsupported")


if __name__ == "__main__":
    unittest.main()