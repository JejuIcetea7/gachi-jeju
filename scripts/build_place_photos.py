"""kordoc로 변환한 HWPX와 드라이브 사진을 장소별 사진·CSV로 통합한다.

실행: python -B scripts/build_place_photos.py
입력: outputs/place_photos/hwp_sources.json 및 각 converted_path의 HWPX
"""

import csv
import argparse
import hashlib
import io
import json
import posixpath
import re
import struct
import zlib
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIRECTORY = PROJECT_ROOT / "data" / "processed"
OUTPUT_DIRECTORY = PROJECT_ROOT / "outputs" / "place_photos"
PHOTOS_DIRECTORY = PROCESSED_DIRECTORY / "photos"
PHOTOS_FILE = PROCESSED_DIRECTORY / "place_photos.csv"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".wmf", ".emf"}
PHOTO_COLUMNS = [
    "photo_id", "place_id", "place_ids", "image_path", "spot_order", "spot_name",
    "description", "source_type", "source_file", "original_image_ref",
    "description_source_file", "description_source_ref", "mapping_status",
    "sha256", "image_format", "width", "height", "size_bytes", "sources",
]


def read_csv_file(path):
    with path.open(encoding="utf-8-sig", newline="") as source_file:
        return list(csv.DictReader(source_file))


def serialize_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def local_name(tag):
    return tag.split("}")[-1]


def read_element_text(element):
    parts = []
    for child in element.iter():
        if local_name(child.tag) == "t" and child.text:
            parts.append(child.text)
        elif local_name(child.tag) == "autoNum":
            parts.append(child.attrib.get("num", ""))
        elif local_name(child.tag) in {"lineBreak", "tab"}:
            parts.append(" ")
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def read_direct_child(element, name):
    for child in element:
        if local_name(child.tag) == name:
            return child
    return None


def read_image_references(element):
    references = []
    for child in element.iter():
        if local_name(child.tag) == "img":
            reference = child.attrib.get("binaryItemIDRef")
            if reference and reference not in references:
                references.append(reference)
    return references


def read_picture_details(element):
    details = defaultdict(list)
    for picture in element.iter():
        if local_name(picture.tag) != "pic":
            continue
        caption = read_direct_child(picture, "caption")
        caption_text = ""
        if caption is not None:
            caption_text = read_element_text(caption)
        comment = read_direct_child(picture, "shapeComment")
        original_name = ""
        if comment is not None:
            match = re.search(r"원본 그림의 이름:\s*([^\r\n]+)", comment.text or "")
            if match:
                original_name = match.group(1).strip()
        for reference in read_image_references(picture):
            details[reference].append({"caption": caption_text, "original_filename": original_name})
    return details


def read_table_cells(table):
    cells = []
    for row in table:
        if local_name(row.tag) != "tr":
            continue
        for cell in row:
            if local_name(cell.tag) != "tc":
                continue
            address = read_direct_child(cell, "cellAddr")
            span = read_direct_child(cell, "cellSpan")
            if address is None or span is None:
                raise ValueError("표 셀의 위치 또는 병합 정보가 없습니다.")
            # 중첩표의 사진·글은 해당 중첩표에서 별도로 연결한다.
            cell_content = ET.fromstring(ET.tostring(cell))
            for parent in list(cell_content.iter()):
                for child in list(parent):
                    if local_name(child.tag) == "tbl":
                        parent.remove(child)
            cells.append({
                "row": int(address.attrib["rowAddr"]),
                "column": int(address.attrib["colAddr"]),
                "row_span": int(span.attrib["rowSpan"]),
                "column_span": int(span.attrib["colSpan"]),
                "text": read_element_text(cell_content),
                "images": read_image_references(cell_content),
            })
    return cells


def find_table_caption(image_cell, cells):
    """열 범위가 같은 바로 아래 셀만 개별 사진 설명으로 연결한다."""
    next_row = image_cell["row"] + image_cell["row_span"]
    candidates = []
    for cell in cells:
        if cell["row"] != next_row or cell["images"] or not cell["text"]:
            continue
        if cell["column"] != image_cell["column"]:
            continue
        if cell["column_span"] != image_cell["column_span"]:
            continue
        candidates.append(cell)
    if len(candidates) == 1:
        return candidates[0]
    return None


def build_table_occurrences(table, location):
    cells = read_table_cells(table)
    title = ""
    for cell in cells:
        if cell["row"] == 0 and not cell["images"] and cell["text"]:
            title = cell["text"]
            break
    column_count = int(table.attrib.get("colCnt", "0"))
    picture_cells = []
    for cell in cells:
        if cell["images"]:
            picture_cells.append(cell)
    common_notes = []
    for cell in cells:
        if cell["images"] or not cell["text"] or cell["row"] == 0:
            continue
        if cell["column_span"] != column_count or column_count < 2:
            continue
        for image_cell in picture_cells:
            if image_cell["row"] < cell["row"]:
                common_notes.append(cell)
                break
    context_rows = []
    for cell in cells:
        if cell["text"] and not cell["images"]:
            context_rows.append({"row": cell["row"], "column": cell["column"], "text": cell["text"]})
    details = read_picture_details(table)
    occurrences = []
    for cell in picture_cells:
        caption = find_table_caption(cell, cells)
        for reference in cell["images"]:
            descriptions = []
            description_refs = []
            status = "unconfirmed"
            if caption is not None:
                descriptions.append(caption["text"])
                description_refs.append(f"{location}/r{caption['row']}c{caption['column']}")
                status = "table_caption"
            for note in common_notes:
                if caption is not None and note == caption:
                    continue
                descriptions.append("[표 공통 설명] " + note["text"])
                description_refs.append(f"{location}/r{note['row']}c{note['column']}")
                if status == "unconfirmed":
                    status = "table_context"
            if not descriptions and title and len(context_rows) > 1:
                descriptions.append("[시설 공통 정보] " + serialize_json(context_rows))
                description_refs.append(location)
                status = "table_context"
            image_details = details.get(reference, [{}])
            occurrences.append({
                "resource_id": reference,
                "source_ref": f"{location}/r{cell['row']}c{cell['column']}",
                "spot_name": title,
                "spot_order": "",
                "description": "\n".join(descriptions),
                "description_source_ref": description_refs,
                "mapping_status": status,
                "picture_details": image_details,
                "context": context_rows,
            })
    return occurrences


def is_section_heading(text):
    return bool(re.match(r"^(?:#|\d+[.)]\s*[가-힣])", text))


def strip_filename_caption(text):
    if re.match(r"^그림\s*\d+", text):
        return ""
    return text


def build_paragraph_occurrences(paragraphs, section_name, initial_heading=""):
    groups = []
    group = {"title": initial_heading, "paragraphs": []}
    for paragraph_index, paragraph in paragraphs:
        text = read_element_text(paragraph)
        references = read_image_references(paragraph)
        if not references and text and is_section_heading(text):
            groups.append(group)
            group = {"title": text, "paragraphs": []}
        group["paragraphs"].append((paragraph_index, paragraph))
    groups.append(group)
    occurrences = []
    for group in groups:
        context = []
        image_paragraphs = []
        for paragraph_index, paragraph in group["paragraphs"]:
            references = read_image_references(paragraph)
            text = read_element_text(paragraph)
            if references:
                image_paragraphs.append((paragraph_index, paragraph))
            elif text and text != group["title"]:
                context.append({"source_ref": f"{section_name}/p{paragraph_index}", "text": text})
        # 명확한 소제목이 없는 도입부는 사진에 설명을 배정하지 않고 근거만 보존한다.
        use_context = bool(group["title"])
        for paragraph_index, paragraph in image_paragraphs:
            details = read_picture_details(paragraph)
            paragraph_text = read_element_text(paragraph)
            for reference in read_image_references(paragraph):
                descriptions = []
                description_refs = []
                for detail in details.get(reference, []):
                    caption = strip_filename_caption(detail["caption"])
                    if caption:
                        descriptions.append(caption)
                        description_refs.append(f"{section_name}/p{paragraph_index}/caption")
                status = "unconfirmed"
                if descriptions:
                    status = "picture_caption"
                inline_text = strip_filename_caption(paragraph_text)
                if inline_text and inline_text not in descriptions:
                    descriptions.append("[사진 문단] " + inline_text)
                    description_refs.append(f"{section_name}/p{paragraph_index}")
                    if status == "unconfirmed":
                        status = "picture_paragraph"
                if use_context:
                    for item in context:
                        descriptions.append("[절 공통 설명] " + item["text"])
                        description_refs.append(item["source_ref"])
                    if context and status == "unconfirmed":
                        status = "section_context"
                occurrences.append({
                    "resource_id": reference,
                    "source_ref": f"{section_name}/p{paragraph_index}",
                    "spot_name": group["title"],
                    "spot_order": "",
                    "description": "\n".join(descriptions),
                    "description_source_ref": description_refs,
                    "mapping_status": status,
                    "picture_details": details.get(reference, []),
                    "paragraph_text": paragraph_text,
                    "context": context,
                })
    return occurrences


def read_hwp_occurrences(archive):
    occurrences = []
    section_names = []
    for name in archive.namelist():
        if re.fullmatch(r"Contents/section\d+\.xml", name):
            section_names.append(name)
    section_names.sort(key=read_section_number)
    for section_name in section_names:
        section = ET.fromstring(archive.read(section_name))
        table_index = 0
        paragraph_block = []
        block_title = ""
        for paragraph_index, paragraph in enumerate(section):
            tables = []
            for child in paragraph.iter():
                if local_name(child.tag) == "tbl":
                    tables.append(child)
            if tables:
                occurrences.extend(build_paragraph_occurrences(paragraph_block, section_name, block_title))
                paragraph_block = []
                block_title = ""
                for table in tables:
                    location = f"{section_name}/table{table_index}"
                    occurrences.extend(build_table_occurrences(table, location))
                    cells = read_table_cells(table)
                    nonempty_texts = []
                    for cell in cells:
                        if cell["text"]:
                            nonempty_texts.append(cell["text"])
                    if len(nonempty_texts) == 1 and len(nonempty_texts[0]) <= 80 and not read_image_references(table):
                        block_title = nonempty_texts[0]
                    table_index += 1
            else:
                paragraph_block.append((paragraph_index, paragraph))
        occurrences.extend(build_paragraph_occurrences(paragraph_block, section_name, block_title))
    return occurrences


def read_section_number(name):
    return int(re.search(r"section(\d+)", name).group(1))


def read_hwp_resources(archive):
    manifest = ET.fromstring(archive.read("Contents/content.hpf"))
    resources = {}
    for item in manifest.iter():
        if local_name(item.tag) != "item":
            continue
        href = item.attrib.get("href", "")
        if Path(href).suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        member = posixpath.normpath(href)
        if member not in archive.namelist():
            member = posixpath.normpath("Contents/" + href)
        if member not in archive.namelist():
            raise ValueError(f"이미지 파일이 없습니다: {href}")
        resources[item.attrib["id"]] = member
    return resources


def read_image_properties(data):
    with Image.open(io.BytesIO(data)) as image:
        properties = {"image_format": image.format, "width": image.width, "height": image.height}
        image.verify()
    return properties


def read_hwp_binary_images(path):
    """변환 누락 검증용으로 원본 HWP 컨테이너의 이미지 스트림을 읽는다."""
    with path.open("rb") as source_file:
        header = source_file.read(512)
        if header[:8] != bytes.fromhex("D0CF11E0A1B11AE1"):
            raise ValueError(f"지원하지 않는 HWP 컨테이너: {path}")
        sector_size = 1 << struct.unpack_from("<H", header, 30)[0]
        mini_sector_size = 1 << struct.unpack_from("<H", header, 32)[0]

        def read_sector(number):
            source_file.seek((number + 1) * sector_size)
            return source_file.read(sector_size)

        sector_ids = list(struct.unpack_from("<109I", header, 76))
        next_sector = struct.unpack_from("<I", header, 68)[0]
        for index in range(struct.unpack_from("<I", header, 72)[0]):
            values = struct.unpack("<" + "I" * (sector_size // 4), read_sector(next_sector))
            sector_ids.extend(values[:-1])
            next_sector = values[-1]
        sector_links = []
        for number in sector_ids[:struct.unpack_from("<I", header, 44)[0]]:
            sector_links.extend(struct.unpack("<" + "I" * (sector_size // 4), read_sector(number)))

        def read_sector_chain(number):
            chunks = []
            visited = set()
            while number < 0xFFFFFFFA:
                if number in visited:
                    raise ValueError("HWP 섹터 연결이 순환합니다.")
                visited.add(number)
                chunks.append(read_sector(number))
                number = sector_links[number]
            return b"".join(chunks)

        directory = read_sector_chain(struct.unpack_from("<I", header, 48)[0])
        entries = []
        for offset in range(0, len(directory), 128):
            entry = directory[offset:offset + 128]
            name_length = struct.unpack_from("<H", entry, 64)[0]
            if entry[66] and name_length >= 2:
                entries.append({
                    "name": entry[:name_length - 2].decode("utf-16-le"), "type": entry[66],
                    "sector": struct.unpack_from("<I", entry, 116)[0],
                    "size": struct.unpack_from("<Q", entry, 120)[0],
                })
        root_entry = next(entry for entry in entries if entry["type"] == 5)
        mini_stream = read_sector_chain(root_entry["sector"])[:root_entry["size"]]
        mini_link_bytes = read_sector_chain(struct.unpack_from("<I", header, 60)[0])
        mini_links = list(struct.unpack("<" + "I" * (len(mini_link_bytes) // 4), mini_link_bytes))
        images = []
        for entry in entries:
            if entry["type"] != 2 or not entry["name"].upper().startswith("BIN"):
                continue
            if Path(entry["name"]).suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            if entry["size"] >= 4096:
                data = read_sector_chain(entry["sector"])[:entry["size"]]
            else:
                chunks = []
                number = entry["sector"]
                visited = set()
                while number < 0xFFFFFFFA:
                    if number in visited:
                        raise ValueError("HWP 작은 섹터 연결이 순환합니다.")
                    visited.add(number)
                    chunks.append(mini_stream[number * mini_sector_size:(number + 1) * mini_sector_size])
                    number = mini_links[number]
                data = b"".join(chunks)[:entry["size"]]
            try:
                data = zlib.decompress(data, -15)
            except zlib.error:
                # HWP는 개별 이미지 스트림을 압축하지 않고 저장하기도 한다.
                pass
            images.append({"member": entry["name"], "sha256": hashlib.sha256(data).hexdigest(), "data": data})
        return images


def add_photo(photos_by_hash, data, extension, source):
    content_hash = hashlib.sha256(data).hexdigest()
    if content_hash not in photos_by_hash:
        properties = read_image_properties(data)
        photos_by_hash[content_hash] = {
            "sha256": content_hash, "data": data, "extension": extension.lower(),
            "properties": properties, "sources": [],
        }
    photos_by_hash[content_hash]["sources"].append(source)


def collect_drive_photos(photos_by_hash, mappings, spots):
    folder_places = defaultdict(set)
    original_photo_rows = defaultdict(list)
    for mapping in mappings:
        if mapping["source_format"] == "hub_number_spots":
            folder = Path(mapping["source_file"]).parent.name
            folder_places[folder].add(mapping["place_id"])
            original_path = PROJECT_ROOT / mapping["source_file"]
            with original_path.open(encoding="cp949", newline="") as original_file:
                for record_number, row in enumerate(csv.DictReader(original_file), start=1):
                    filename = row.get("이미지파일명", row.get("이미지파일", "")).strip()
                    if filename:
                        original_photo_rows[(folder, filename)].append({
                            "source_file": mapping["source_file"], "record_number": record_number,
                            "original_id": row.get("아이디", ""), "fields": row,
                        })
    spots_by_key = defaultdict(list)
    for spot in spots:
        if spot["image_file"]:
            folder = Path(spot["source_file"]).parent.name
            spots_by_key[(folder, spot["image_file"])].append(spot)
    source_count = 0
    matched_spots = set()
    image_root = PROJECT_ROOT / "data/raw/drive_images/images"
    for path in sorted(image_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        candidates = folder_places[path.parent.name]
        if len(candidates) != 1:
            raise ValueError(f"사진의 장소를 확정하지 못했습니다: {path}")
        place_id = next(iter(candidates))
        matches = spots_by_key.get((path.parent.name, path.name), [])
        records = []
        for spot in matches:
            matched_spots.add((spot["place_id"], spot["source_file"], spot["order"], spot["image_file"]))
            records.append({
                "place_id": place_id, "source_type": "drive", "source_file": str(path.relative_to(PROJECT_ROOT)),
                "source_ref": path.name, "original_filename": path.name,
                "spot_order": spot["order"], "spot_name": spot["name"],
                "description": spot["description"],
                "description_source_file": "data/processed/place_spots.csv",
                "description_source_ref": [f"place_id={place_id};order={spot['order']};source_file={spot['source_file']}"],
                "original_csv_file": spot["source_file"],
                "original_csv_photo_rows": original_photo_rows.get((path.parent.name, path.name), []),
                "mapping_status": "csv_filename", "context": [],
            })
        if not records:
            records.append({
                "place_id": place_id, "source_type": "drive", "source_file": str(path.relative_to(PROJECT_ROOT)),
                "source_ref": path.name, "original_filename": path.name,
                "spot_order": "", "spot_name": "", "description": "",
                "description_source_file": "", "description_source_ref": [],
                "mapping_status": "unconfirmed", "context": [],
                "original_csv_photo_rows": original_photo_rows.get((path.parent.name, path.name), []),
            })
        data = path.read_bytes()
        for record in records:
            add_photo(photos_by_hash, data, path.suffix, record)
        source_count += 1
    expected_spots = set()
    for spot in spots:
        if spot["image_file"]:
            expected_spots.add((spot["place_id"], spot["source_file"], spot["order"], spot["image_file"]))
    if expected_spots != matched_spots:
        raise ValueError(f"CSV 사진 연결 누락: {sorted(expected_spots - matched_spots)}")
    return {"drive_files": source_count, "csv_references_matched": len(matched_spots)}


def collect_hwp_photos(photos_by_hash, manifest):
    resource_count = 0
    reference_count = 0
    unplaced = []
    original_stream_count = 0
    preserved_original_count = 0
    supplemental_count = 0
    for document in manifest:
        archive_path = PROJECT_ROOT / document["converted_path"]
        with zipfile.ZipFile(archive_path) as archive:
            original_images = read_hwp_binary_images(PROJECT_ROOT / document["source_file"])
            original_by_hash = defaultdict(list)
            for original_image in original_images:
                original_by_hash[original_image["sha256"]].append(original_image["member"])
            original_stream_count += len(original_images)
            converted_hashes = set()
            resources = read_hwp_resources(archive)
            occurrences = read_hwp_occurrences(archive)
            by_resource = defaultdict(list)
            for occurrence in occurrences:
                if occurrence["resource_id"] not in resources:
                    raise ValueError(f"이미지 참조 연결 실패: {document['place_id']} {occurrence['resource_id']}")
                by_resource[occurrence["resource_id"]].append(occurrence)
            for resource_id, member in resources.items():
                matches = by_resource.get(resource_id, [])
                if not matches:
                    matches = [{
                        "resource_id": resource_id, "source_ref": "unreferenced_resource",
                        "spot_order": "", "spot_name": "", "description": "",
                        "description_source_ref": [], "mapping_status": "unconfirmed",
                        "picture_details": [], "context": [],
                    }]
                    unplaced.append({"place_id": document["place_id"], "resource_id": resource_id})
                data = archive.read(member)
                content_hash = hashlib.sha256(data).hexdigest()
                converted_hashes.add(content_hash)
                for occurrence in matches:
                    source = dict(occurrence)
                    source.update({
                        "place_id": document["place_id"], "source_type": document["source_type"],
                        "source_file": document["source_file"], "binary_member": member,
                        "converted_path": document["converted_path"],
                        "description_source_file": document["source_file"],
                        "original_binary_members": original_by_hash.get(content_hash, []),
                        "extraction_method": "kordoc_hwpx",
                    })
                    add_photo(photos_by_hash, data, Path(member).suffix, source)
                    reference_count += 1
                resource_count += 1
            for original_image in original_images:
                if original_image["sha256"] not in converted_hashes:
                    source = {
                        "place_id": document["place_id"], "source_type": document["source_type"],
                        "source_file": document["source_file"], "source_ref": "original_binary/" + original_image["member"],
                        "original_filename": original_image["member"], "spot_order": "", "spot_name": "",
                        "description": "", "description_source_file": "", "description_source_ref": [],
                        "mapping_status": "unconfirmed", "context": [],
                        "extraction_method": "original_hwp_supplement",
                        "original_binary_members": [original_image["member"]],
                    }
                    add_photo(photos_by_hash, original_image["data"], Path(original_image["member"]).suffix, source)
                    supplemental_count += 1
                if original_image["sha256"] not in photos_by_hash:
                    raise ValueError("HWP 원본 이미지 보존 검증 실패")
                preserved_original_count += 1
        print(f"HWP 수집: {document['place_id']} / 이미지 {len(resources)}개", flush=True)
    return {
        "hwp_documents": len(manifest), "hwp_resources": resource_count,
        "hwp_occurrences": reference_count, "unplaced_resources": unplaced,
        "hwp_original_streams": original_stream_count,
        "hwp_original_streams_preserved": preserved_original_count,
        "hwp_supplemental_streams": supplemental_count,
    }


def collect_unique_values(sources, key):
    values = []
    for source in sources:
        value = source.get(key)
        if value and value not in values:
            values.append(value)
    return values


def read_photo_sort_key(photo):
    first_source = photo["sources"][0]
    return first_source["place_id"], first_source["source_file"], first_source["source_ref"]


def build_photo_rows(photos_by_hash, existing_rows):
    rows = []
    place_counters = Counter()
    existing_by_hash = {}
    for row in existing_rows:
        existing_by_hash[row["sha256"]] = row
        number = int(row["photo_id"].rsplit("_", 1)[1])
        place_counters[row["place_id"]] = max(place_counters[row["place_id"]], number)
    if not set(existing_by_hash).issubset(photos_by_hash):
        raise ValueError("기존 사진이 입력에서 사라졌습니다. 자동 삭제하지 않습니다.")
    photos = sorted(photos_by_hash.values(), key=read_photo_sort_key)
    for photo in photos:
        sources = photo["sources"]
        place_ids = sorted(collect_unique_values(sources, "place_id"))
        place_id = sources[0]["place_id"]
        existing_row = existing_by_hash.get(photo["sha256"])
        if existing_row:
            photo_id = existing_row["photo_id"]
            place_id = existing_row["place_id"]
            image_path = PROJECT_ROOT / existing_row["image_path"]
            if not image_path.is_relative_to(PHOTOS_DIRECTORY):
                raise ValueError("기존 사진 경로가 통합 폴더 밖에 있습니다.")
        else:
            place_counters[place_id] += 1
            photo_id = f"{place_id}_{place_counters[place_id]:04d}"
            image_path = PHOTOS_DIRECTORY / place_id / (photo_id + photo["extension"])
        image_path.parent.mkdir(parents=True, exist_ok=True)
        if image_path.exists():
            if hashlib.sha256(image_path.read_bytes()).hexdigest() != photo["sha256"]:
                raise ValueError(f"기존 사진 내용 불일치: {image_path}")
        else:
            with image_path.open("xb") as output_file:
                output_file.write(photo["data"])
        descriptions = []
        for source in sources:
            description = source["description"]
            if not description:
                continue
            label = f"[{source['source_type']}:{source['source_file']}#{source['source_ref']}]"
            descriptions.append(label + " " + description)
        description = "설명 없음"
        if descriptions:
            description = "\n\n".join(descriptions)
        row = {
            "photo_id": photo_id, "place_id": place_id, "place_ids": serialize_json(place_ids),
            "image_path": str(image_path.relative_to(PROJECT_ROOT)),
            "spot_order": serialize_json(collect_unique_values(sources, "spot_order")),
            "spot_name": " / ".join(collect_unique_values(sources, "spot_name")),
            "description": description,
            "source_type": serialize_json(collect_unique_values(sources, "source_type")),
            "source_file": serialize_json(collect_unique_values(sources, "source_file")),
            "original_image_ref": serialize_json([
                {"source_file": source["source_file"], "source_ref": source["source_ref"],
                 "resource_id": source.get("resource_id", ""), "original_filename": source.get("original_filename", ""),
                 "picture_details": source.get("picture_details", [])} for source in sources
            ]),
            "description_source_file": serialize_json(collect_unique_values(sources, "description_source_file")),
            "description_source_ref": serialize_json(collect_unique_values(sources, "description_source_ref")),
            "mapping_status": serialize_json(collect_unique_values(sources, "mapping_status")),
            "sha256": photo["sha256"], "size_bytes": len(photo["data"]),
            "sources": serialize_json(sources),
        }
        row.update(photo["properties"])
        rows.append(row)
        del photo["data"]
    return rows


def verify_photo_rows(rows, places):
    known_places = set()
    for place in places:
        known_places.add(place["place_id"])
    photo_ids = set()
    hashes = set()
    covered_places = set()
    statuses = Counter()
    no_description = 0
    cross_place_photos = []
    for row in rows:
        if row["photo_id"] in photo_ids or row["sha256"] in hashes:
            raise ValueError("사진 ID 또는 파일 내용이 중복됩니다.")
        photo_ids.add(row["photo_id"])
        hashes.add(row["sha256"])
        path = PROJECT_ROOT / row["image_path"]
        if path.stem != row["photo_id"]:
            raise ValueError(f"파일명과 사진 ID가 일치하지 않습니다: {path}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError(f"사진 내용이 일치하지 않습니다: {path}")
        place_ids = json.loads(row["place_ids"])
        if not set(place_ids).issubset(known_places):
            raise ValueError(f"등록되지 않은 장소입니다: {place_ids}")
        covered_places.update(place_ids)
        if len(place_ids) > 1:
            cross_place_photos.append({"photo_id": row["photo_id"], "place_ids": place_ids})
        if row["description"] == "설명 없음":
            no_description += 1
        for source in json.loads(row["sources"]):
            statuses[source["mapping_status"]] += 1
    disk_paths = set(PHOTOS_DIRECTORY.rglob("*.*"))
    listed_paths = {PROJECT_ROOT / row["image_path"] for row in rows}
    if disk_paths != listed_paths:
        raise ValueError("사진 디렉터리와 CSV가 일치하지 않습니다.")
    return {
        "unique_photos": len(rows), "places_with_photos": len(covered_places),
        "places_without_photos": sorted(known_places - covered_places),
        "photos_without_description": no_description, "source_mapping_statuses": dict(statuses),
        "cross_place_photos": cross_place_photos, "sha256_verified": len(rows),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-metadata", action="store_true", help="기존 사진 ID를 유지하면서 연결 정보와 추가 원본을 갱신합니다.")
    arguments = parser.parse_args()
    if (PHOTOS_FILE.exists() or PHOTOS_DIRECTORY.exists()) and not arguments.refresh_metadata:
        raise FileExistsError("기존 사진·CSV는 덮어쓰지 않습니다. 출력 경로를 확인해 주세요.")
    with (OUTPUT_DIRECTORY / "hwp_sources.json").open(encoding="utf-8") as source_file:
        manifest = json.load(source_file)
    for document in manifest:
        if not (PROJECT_ROOT / document["converted_path"]).is_file():
            raise FileNotFoundError(document["converted_path"])
    places = read_csv_file(PROCESSED_DIRECTORY / "places.csv")
    mappings = read_csv_file(PROCESSED_DIRECTORY / "place_id_map.csv")
    spots = read_csv_file(PROCESSED_DIRECTORY / "place_spots.csv")
    photos_by_hash = {}
    report = collect_drive_photos(photos_by_hash, mappings, spots)
    report.update(collect_hwp_photos(photos_by_hash, manifest))
    existing_rows = []
    if arguments.refresh_metadata:
        existing_rows = read_csv_file(PHOTOS_FILE)
    rows = build_photo_rows(photos_by_hash, existing_rows)
    report.update(verify_photo_rows(rows, places))
    temporary_csv = PHOTOS_FILE.with_suffix(".csv.tmp")
    with temporary_csv.open("x", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=PHOTO_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    temporary_csv.replace(PHOTOS_FILE)
    with (OUTPUT_DIRECTORY / "verification.json").open("w", encoding="utf-8") as output_file:
        json.dump(report, output_file, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
