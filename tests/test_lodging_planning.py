import copy
import json
import unittest

from pydantic import ValidationError

from backend.app.routers.chat import ChatOptions, ChatRequest, build_chat_messages
from backend.app.services.lodging_planning import (
    LodgingStay, build_lodging_warnings, retain_lodging_state, validate_lodging_boundaries,
    validate_lodging_selections,
)
from backend.app.services.plan_retry import build_plan_retry_messages, validate_retry_options
from backend.app.services.route_endpoints import validate_route_endpoints


ORIGIN = {"latitude": 33.4, "longitude": 126.5}
SITE = {"latitude": 33.41, "longitude": 126.51}
HOTEL = {"latitude": 33.45, "longitude": 126.55}
OTHER_HOTEL = {"latitude": 33.48, "longitude": 126.58}


def stay(night=1, point=None):
    return {"night": night, "name": "선택한 숙소", "address": "제주시 테스트 도로 1", **(point or HOTEL)}


def visit():
    return {"item_type": "visit", "title": "관광지", "verified": True, **SITE}


def move(identifier):
    return {"item_type": "move", "title": "이동", "route_id": identifier}


def route(identifier, origin, destination):
    return {"route_id": identifier, "origin": dict(origin), "destination": dict(destination)}


def two_day_plan():
    return {"days": [
        {"day": 1, "items": [move("first"), visit(), move("hotel_in")]},
        {"day": 2, "items": [move("hotel_out"), visit(), move("last")]},
    ]}


def two_day_routes():
    return [route("first", ORIGIN, SITE), route("hotel_in", SITE, HOTEL),
            route("hotel_out", HOTEL, SITE), route("last", SITE, ORIGIN)]


class LodgingOptionTests(unittest.TestCase):
    def test_old_requests_keep_optional_lodging_fields(self):
        options = ChatOptions()
        self.assertIsNone(options.lodging_status)
        self.assertEqual(options.lodging_stays, [])

    def test_undecided_lodging_is_sent_even_without_history(self):
        request = ChatRequest(message="관광지를 바꿔 주세요.", options={
            "trip_days": 3, "lodging_status": "undecided",
        })
        before = request.model_dump(mode="json")
        payload = json.loads(build_chat_messages(request)[-1]["content"])
        self.assertEqual(payload["options"]["lodging_status"], "undecided")
        self.assertEqual(payload["options"]["lodging_stays"], [])
        self.assertEqual(request.model_dump(mode="json"), before)

    def test_partial_choices_are_allowed_without_guessing_other_nights(self):
        options = ChatOptions(trip_days=4, lodging_status="undecided", lodging_stays=[stay(2)])
        self.assertEqual([item.night for item in options.lodging_stays], [2])

    def test_same_hotel_can_be_selected_for_multiple_nights(self):
        options = ChatOptions(trip_days=3, lodging_status="provided", lodging_stays=[stay(), stay(2)])
        self.assertEqual(len(options.lodging_stays), 2)

    def test_duplicate_nights_and_dates_outside_trip_are_rejected(self):
        for days, stays in ((3, [stay(), stay()]), (3, [stay(3)]), (1, [stay()])):
            with self.subTest(days=days, stays=stays):
                with self.assertRaises(ValidationError):
                    ChatOptions(trip_days=days, lodging_status="provided", lodging_stays=stays)

    def test_stays_cannot_be_sent_without_an_applicable_status(self):
        for status in (None, "not_needed"):
            with self.subTest(status=status):
                with self.assertRaises(ValidationError):
                    ChatOptions(trip_days=3, lodging_status=status, lodging_stays=[stay()])

    def test_selected_stay_rejects_bad_values_and_unconfirmed_claim_fields(self):
        for changes in ({"night": True}, {"night": 0}, {"night": "1"}, {"name": " "},
                        {"latitude": float("nan")}, {"latitude": 37.5}, {"longitude": None},
                        {"latitude": True}, {"accessible": True}, {"place_id": "P001"}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):
                    LodgingStay.model_validate({**stay(), **changes})

    def test_map_url_must_match_provider_id(self):
        good = {**stay(), "provider_place_id": "100", "place_url": "https://place.map.kakao.com/100"}
        self.assertEqual(LodgingStay.model_validate(good).provider_place_id, "100")
        for changes in ({"place_url": "javascript:invalid"}, {"provider_place_id": None}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):
                    LodgingStay.model_validate({**good, **changes})

    def test_missing_response_status_preserves_previous_choice_and_does_not_mutate_inputs(self):
        previous = {"lodging_status": "undecided", "lodging_stays": [stay()]}
        current = {"trip_days": 3, "lodging_status": None, "lodging_stays": []}
        originals = copy.deepcopy((previous, current))
        restored = retain_lodging_state(current, previous)
        self.assertEqual(restored["lodging_status"], "undecided")
        self.assertEqual(restored["lodging_stays"], [stay()])
        restored["lodging_stays"][0]["name"] = "변경"
        self.assertEqual((previous, current), originals)

    def test_explicit_change_can_clear_previous_stays(self):
        previous = {"lodging_status": "provided", "lodging_stays": [stay()]}
        current = {"lodging_status": "undecided", "lodging_stays": []}
        self.assertEqual(retain_lodging_state(current, previous), current)

    def test_shorter_trip_drops_stays_outside_new_trip(self):
        previous = {"lodging_status": "provided", "lodging_stays": [stay(), stay(2)]}
        restored = retain_lodging_state({"trip_days": 2, "lodging_status": None}, previous)
        self.assertEqual([item["night"] for item in restored["lodging_stays"]], [1])

    def test_retry_keeps_stays_and_rejects_location_or_state_changes(self):
        options = {"lodging_status": "provided", "lodging_stays": [stay(), stay(2, OTHER_HOTEL)]}
        reordered = {**options, "lodging_stays": list(reversed(options["lodging_stays"]))}
        self.assertEqual(validate_retry_options(options, reordered), [])
        for changed in ({**options, "lodging_status": "undecided"},
                        {**options, "lodging_stays": [stay(1, OTHER_HOTEL), stay(2, OTHER_HOTEL)]}):
            self.assertEqual(len(validate_retry_options(options, changed)), 1)
        messages = [{"role": "user", "content": json.dumps({"message": "추천해 주세요."})}]
        retried = build_plan_retry_messages(messages, failed_plan={"days": []}, options=options, errors=["위치 오류"])
        locked = json.loads(retried[-1]["content"])["plan_retry"]["locked_options"]
        self.assertEqual(locked, options)


class LodgingSelectionEvidenceTests(unittest.TestCase):
    def trace(self, **changes):
        return [{"tool": "find_location", "input": {"keyword": "선택한 숙소"}, "output": {
            "status": "ok", "name": "선택한 숙소", "address": "제주시 테스트 도로 1", **HOTEL, **changes,
        }}]

    def check(self, previous=None, current=None, message="선택한 숙소에서 묵습니다.", trace=None):
        return validate_lodging_selections(
            previous or {"lodging_stays": []}, current or {"lodging_stays": [stay()]},
            user_message=message, tool_trace=self.trace() if trace is None else trace,
        )

    def test_user_selected_stay_needs_no_additional_location_call(self):
        self.assertEqual(self.check(previous={"lodging_stays": [stay()]}, trace=[]), [])

    def test_user_named_stay_with_current_location_evidence_is_allowed(self):
        self.assertEqual(self.check(), [])

    def test_user_provided_full_address_can_identify_stay(self):
        self.assertEqual(self.check(message="숙소 주소는 제주시 테스트 도로 1입니다."), [])

    def test_coordinate_rounding_is_allowed_but_different_locations_are_not(self):
        rounded = {"lodging_stays": [{**stay(), "latitude": HOTEL["latitude"] + 0.0000001}]}
        self.assertEqual(self.check(current=rounded), [])
        self.assertEqual(self.check(previous={"lodging_stays": [stay()]}, current=rounded, trace=[]), [])
        changed = {"lodging_stays": [{**stay(), "latitude": HOTEL["latitude"] + 0.0001}]}
        self.assertEqual(len(self.check(current=changed)), 1)

    def test_lookup_without_user_named_stay_is_not_a_selection(self):
        self.assertEqual(len(self.check(message="숙소는 아직 미정입니다.")), 1)

    def test_name_without_current_location_evidence_is_not_enough(self):
        for trace in ([], [None], [{"tool": "find_location", "output": None}],
                      self.trace(status="not_found"), self.trace(latitude=33.5),
                      self.trace(name="다른 숙소")):
            with self.subTest(trace=trace):
                self.assertEqual(len(self.check(trace=trace)), 1)

    def test_new_night_or_changed_location_needs_current_evidence(self):
        self.assertEqual(len(self.check(previous={"lodging_stays": [stay(2)]}, trace=[])), 1)
        self.assertEqual(len(self.check(previous={"lodging_stays": [stay(point=OTHER_HOTEL)]}, trace=[])), 1)

    def test_map_id_cannot_be_invented_from_location_lookup(self):
        selected = {"lodging_stays": [{**stay(), "provider_place_id": "100"}]}
        self.assertEqual(len(self.check(current=selected)), 1)


class LodgingBoundaryTests(unittest.TestCase):
    def check(self, plan, stays=None, status="undecided", days=2):
        return validate_lodging_boundaries(plan, trip_days=days, lodging_status=status, lodging_stays=stays or [])

    def test_tourism_only_days_with_undecided_lodging_are_allowed(self):
        plan = {"days": [{"day": 1, "items": [move("first"), visit()]},
                         {"day": 2, "items": [visit(), move("last")]}]}
        self.assertEqual(self.check(plan), [])

    def test_unselected_lodging_cannot_supply_arrival_or_next_departure_move(self):
        errors = self.check(two_day_plan())
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("미정" in error for error in errors))

    def test_selected_lodging_moves_must_be_present(self):
        plan = {"days": [{"day": 1, "items": [move("first"), visit()]},
                         {"day": 2, "items": [visit(), move("last")]}]}
        self.assertEqual(len(self.check(plan, [stay()])), 2)

    def test_partial_choices_do_not_allow_moves_for_other_nights(self):
        plan = {"days": [
            {"day": 1, "items": [visit(), move("hotel1")]},
            {"day": 2, "items": [move("from1"), visit(), move("unknown2")]},
            {"day": 3, "items": [move("unknown3"), visit()]},
        ]}
        errors = self.check(plan, [stay()], days=3)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("2일차" in error for error in errors))
        self.assertTrue(any("3일차" in error for error in errors))

    def test_empty_days_do_not_get_fabricated_lodging_routes(self):
        plan = {"days": [{"day": 1, "items": []}, {"day": 2, "items": []}]}
        self.assertEqual(self.check(plan, [stay()]), [])

    def test_old_requests_and_no_overnight_requests_keep_previous_behavior(self):
        for status, days in ((None, 2), ("not_needed", 2), ("undecided", 1)):
            self.assertEqual(self.check(two_day_plan(), status=status, days=days), [])

    def test_warning_counts_missing_nights_without_changing_plan(self):
        options = {"trip_days": 4, "lodging_status": "undecided", "lodging_stays": [stay(2)]}
        before = copy.deepcopy(options)
        warnings = build_lodging_warnings(**options)
        self.assertEqual(len(warnings), 1)
        self.assertIn("2박", warnings[0])
        self.assertEqual(options, before)

    def test_no_warning_for_complete_stays_or_non_overnight_trip(self):
        for days, status, choices in ((2, "provided", [stay()]), (1, "undecided", []),
                                     (3, "not_needed", []), (3, None, [])):
            self.assertEqual(build_lodging_warnings(trip_days=days, lodging_status=status, lodging_stays=choices), [])


class LodgingEndpointTests(unittest.TestCase):
    def check(self, plan=None, records=None, stays=None):
        return validate_route_endpoints(
            plan or two_day_plan(), records if records is not None else two_day_routes(),
            departure=ORIGIN, return_to_origin=True, lodging_stays=stays if stays is not None else [stay()],
        )

    def test_selected_stay_anchors_both_days_and_preserves_inputs(self):
        plan, records, stays = two_day_plan(), two_day_routes(), [stay()]
        before = copy.deepcopy((plan, records, stays))
        self.assertEqual(self.check(plan, records, stays), [])
        self.assertEqual((plan, records, stays), before)

    def test_wrong_hotel_arrival_is_rejected(self):
        records = two_day_routes()
        records[1]["destination"] = OTHER_HOTEL
        errors = self.check(records=records)
        self.assertEqual(len(errors), 1)
        self.assertIn("도착 위치", errors[0])

    def test_next_morning_cannot_start_at_original_departure(self):
        records = two_day_routes()
        records[2]["origin"] = ORIGIN
        errors = self.check(records=records)
        self.assertEqual(len(errors), 1)
        self.assertIn("출발 위치", errors[0])

    def test_each_nights_own_hotel_is_used(self):
        plan = {"days": [
            {"day": 1, "items": [move("first"), visit(), move("hotel1")]},
            {"day": 2, "items": [move("from1"), visit(), move("hotel2")]},
            {"day": 3, "items": [move("from2"), visit(), move("last")]},
        ]}
        records = [route("first", ORIGIN, SITE), route("hotel1", SITE, HOTEL),
                   route("from1", HOTEL, SITE), route("hotel2", SITE, OTHER_HOTEL),
                   route("from2", OTHER_HOTEL, SITE), route("last", SITE, ORIGIN)]
        self.assertEqual(self.check(plan, records, [stay(), stay(2, OTHER_HOTEL)]), [])

    def test_last_return_still_checks_original_departure(self):
        records = two_day_routes()
        records[-1]["destination"] = HOTEL
        errors = self.check(records=records)
        self.assertEqual(len(errors), 1)
        self.assertIn("도착 위치", errors[0])


if __name__ == "__main__":
    unittest.main()
