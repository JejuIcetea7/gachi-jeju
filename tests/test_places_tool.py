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

if __name__ == "__main__":
    unittest.main()
