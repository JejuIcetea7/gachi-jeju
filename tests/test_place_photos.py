import unittest
import xml.etree.ElementTree as ET

from scripts.build_place_photos import (
    build_paragraph_occurrences,
    build_table_occurrences,
    find_table_caption,
    read_picture_details,
    is_section_heading,
)


def make_cell(row, column, column_span=1, text="", images=None):
    if images is None:
        images = []
    return {
        "row": row, "column": column, "row_span": 1,
        "column_span": column_span, "text": text, "images": images,
    }


class PlacePhotoTests(unittest.TestCase):
    def test_caption_uses_same_column(self):
        image_cell = make_cell(1, 0, images=["image3"])
        left = make_cell(2, 0, text="화장실 표지판")
        right = make_cell(2, 1, text="화장실 입구")
        self.assertEqual(find_table_caption(image_cell, [image_cell, right, left]), left)

    def test_merged_caption_is_not_assigned_to_one_photo(self):
        image_cell = make_cell(1, 0, images=["image3"])
        common = make_cell(2, 0, column_span=2, text="공통 안내")
        self.assertIsNone(find_table_caption(image_cell, [image_cell, common]))

    def test_caption_row_cannot_contain_another_image(self):
        image_cell = make_cell(1, 0, images=["image3"])
        next_image = make_cell(2, 0, text="다른 사진", images=["image4"])
        self.assertIsNone(find_table_caption(image_cell, [image_cell, next_image]))

    def test_picture_number_is_distinct_from_resource_number(self):
        picture = ET.fromstring('''<p><pic><img binaryItemIDRef="image7"/>
            <caption><t>그림 </t><autoNum num="6"/><t> DSC07123.JPG</t></caption>
            <shapeComment>원본 그림의 이름: DSC07123.JPG</shapeComment></pic></p>''')
        detail = read_picture_details(picture)["image7"][0]
        self.assertEqual(detail["caption"], "그림 6 DSC07123.JPG")
        self.assertEqual(detail["original_filename"], "DSC07123.JPG")

    def test_section_context_stops_at_next_facility(self):
        xml = '''<section>
            <p><t>#화장실</t></p>
            <p><pic><img binaryItemIDRef="image1"/></pic></p>
            <p><t>◎ 접근성 정보:</t></p>
            <p><t>출입문 폭 84cm</t></p>
            <p><t>#주차장</t></p>
            <p><pic><img binaryItemIDRef="image2"/></pic></p>
            <p><t>장애인 주차 2면</t></p>
        </section>'''
        section = ET.fromstring(xml)
        rows = build_paragraph_occurrences(list(enumerate(section)), "section0")
        self.assertIn("출입문 폭 84cm", rows[0]["description"])
        self.assertNotIn("장애인 주차", rows[0]["description"])
        self.assertEqual(rows[1]["spot_name"], "#주차장")
        self.assertNotIn("출입문", rows[1]["description"])

    def test_nested_table_image_is_not_assigned_parent_caption(self):
        xml = '''<tbl colCnt="1"><tr><tc><cellAddr rowAddr="0" colAddr="0"/>
            <cellSpan rowSpan="1" colSpan="1"/>
            <tbl colCnt="1"><tr><tc><cellAddr rowAddr="0" colAddr="0"/>
            <cellSpan rowSpan="1" colSpan="1"/><pic><img binaryItemIDRef="image1"/></pic>
            </tc></tr></tbl></tc></tr>
            <tr><tc><cellAddr rowAddr="1" colAddr="0"/>
            <cellSpan rowSpan="1" colSpan="1"/><t>다른 안내</t></tc></tr></tbl>'''
        rows = build_table_occurrences(ET.fromstring(xml), "table0")
        self.assertEqual(rows, [])

    def test_picture_paragraph_text_is_preserved(self):
        paragraph = ET.fromstring('<p><pic><img binaryItemIDRef="image6"/></pic><t>2층 15도 경사, 250cm</t></p>')
        rows = build_paragraph_occurrences([(3, paragraph)], "section0", "2층 전시실")
        self.assertIn("15도 경사", rows[0]["description"])
        self.assertEqual(rows[0]["mapping_status"], "picture_paragraph")

    def test_table_heading_provides_paragraph_context(self):
        section = ET.fromstring('<section><p><pic><img binaryItemIDRef="image1"/></pic></p><p><t>출입문 폭 84cm</t></p></section>')
        rows = build_paragraph_occurrences(list(enumerate(section)), "section0", "장애인화장실 정보")
        self.assertEqual(rows[0]["spot_name"], "장애인화장실 정보")
        self.assertIn("84cm", rows[0]["description"])

    def test_numbered_facility_heading_is_not_a_decimal_measurement(self):
        self.assertTrue(is_section_heading("1.주차장"))
        self.assertFalse(is_section_heading("2.5m 경사로"))


if __name__ == "__main__":
    unittest.main()
