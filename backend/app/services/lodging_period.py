"""당일 여행의 숙박 요청과 기간 연장 동의를 구분합니다."""

from copy import deepcopy
from datetime import date
import re
import unicodedata


PERIOD_QUESTION = "현재 여행은 당일 일정입니다. 숙박을 포함하도록 1박 2일로 늘릴까요?"
KEEP_DAY_TRIP_REPLY = "당일 일정을 유지하겠습니다. 숙박과 다음 날 이동은 추가하지 않았습니다."
DATE_REQUIRED_REPLY = "현재 출발일로는 1박 2일을 설정할 수 없습니다. 다른 출발일을 알려 주세요."


def _compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value)).casefold()


def _positive_action(pattern, text):
    for match in re.finditer(pattern, text):
        start, end = match.span("action")
        before = text[max(match.start(), start - 2):start]
        after = text[end:end + 12]
        if before.endswith("안") or "필요없" in text[match.start():start] or re.match(
            r"(?:없이|하지(?:마|말|않)|지(?:마|말|않)|(?:은|는)?안)", after,
        ):
            continue
        return True
    return False


def _explicit_period(text):
    for match in re.finditer(r"(\d+)박(\d+)일|(\d+)일(?:간|동안|여행|일정|로)", text):
        days = int(match.group(2) or match.group(3))
        if days >= 2 and not re.match(r"(?:은|는)?(?:아니|싫|말고)", text[match.end():]):
            return True
    if "일박이일" in text or "이틀로" in text or "이틀여행" in text:
        return True
    if _positive_action(
        r"(?:여행기간|기간|여행날짜|마지막날|날짜)[^.,!?;]{0,25}?"
        r"(?P<action>늘려|늘리|연장|변경|바꿔|바꾸)", text,
    ):
        return True
    iso = re.search(r"(\d{4}-\d{2}-\d{2})(?:~|부터|에서)(\d{4}-\d{2}-\d{2})", text)
    if iso and iso.group(1) != iso.group(2):
        return True
    korean = re.search(
        r"(?:(\d{1,2})월)?(\d{1,2})일?(?:~|부터|에서)(?:(\d{1,2})월)?(\d{1,2})일", text,
    )
    return bool(korean and (
        (korean.group(3) and korean.group(1) != korean.group(3))
        or korean.group(2) != korean.group(4)
    ))


def _lodging_action(text):
    if re.search(r"방법|절차|어떻게|(?:뜻|의미)(?:을|를)?(?:알려|설명)|체크인시간|체크아웃시간", text):
        return False
    patterns = (
        r"(?:숙소|숙박지)(?:를|는|을|은)?[^.,!?;]{0,30}?"
        r"(?P<action>변경|바꿔|바꾸|추가|정해|선택|예약|잡아|잡고|도착|출발|묵|머물)",
        r"호텔(?:을|를)[^.,!?;]{0,24}?(?P<action>변경|바꿔|바꾸|예약|잡아|선택)",
        r"(?:호텔|리조트|펜션|민박|게스트하우스)(?:에서|에)(?:오늘밤|하룻밤|1박|일박)?"
        r"(?P<action>묵|자고|자겠|잘|머물|숙박)",
        r"(?:숙박|하룻밤|1박|일박)(?:을|은|이|를)?"
        r"(?P<action>추가|포함|하고|하겠|할게|할래|하려|원해|원합니다|자고|잘|묵|머물)",
    )
    return any(_positive_action(pattern, text) for pattern in patterns)


def lodging_period_step(message, options, history):
    """짧은 동의·거절은 바로 앞의 정해진 기간 질문에 대한 답변일 때만 해석합니다."""
    text = _compact(message)
    answer = re.sub(r"[,!。！.]", "", text)
    pending = None
    if isinstance(history, list) and len(history) >= 2:
        previous, last = history[-2:]
        if (
            isinstance(previous, dict) and isinstance(last, dict)
            and previous.get("role") == "user" and isinstance(previous.get("content"), str)
            and last.get("role") == "assistant" and last.get("content") == PERIOD_QUESTION
        ):
            pending = previous["content"]
    if pending is not None:
        negative = {
            "아니요", "아뇨", "아니", "당일유지", "당일로유지", "당일로유지해주세요",
            "당일로유지해줘", "그대로유지해주세요", "연장하지마세요", "숙박안할게요",
        }
        if answer in negative and options.get("trip_days") == 1:
            return {"action": "keep"}
        affirmative = {
            "네", "예", "응", "그래", "좋아", "좋아요", "그렇게해주세요",
            "늘려주세요", "연장해주세요", "동의합니다",
        }
        if answer in affirmative or re.fullmatch(
            r"(?:네|예|응)?(?:1박2일|이틀)(?:로)?(?:늘려주세요|늘려줘|해주세요|해줘|바꿔주세요)", answer,
        ):
            return {"action": "accepted", "original_request": pending}
        if not answer.startswith(("아니", "아뇨")) and _explicit_period(text):
            return {"action": "specified", "original_request": pending}
    if options.get("trip_days") == 1 and _lodging_action(text) and not _explicit_period(text):
        return {"action": "ask"}
    return {"action": "pass"}


def prepare_lodging_period_payload(payload, history):
    prepared = deepcopy(payload)
    step = lodging_period_step(payload["message"], payload["options"], history)
    if step["action"] == "accepted" and payload["options"].get("trip_days") in (None, 1):
        start = payload["options"].get("start_date")
        if start is not None and date.fromisoformat(start) == date.max:
            step = {"action": "date_required"}
        else:
            prepared["options"].update(
                trip_days=2, lodging_status="undecided", lodging_stays=[],
            )
    if step["action"] != "pass":
        prepared["lodging_period_confirmation"] = step
    return prepared


def lodging_period_user_message(message, options, history):
    step = lodging_period_step(message, options, history)
    if step["action"] in ("accepted", "specified"):
        return step["original_request"] + "\n\n사용자 확인: " + message
    return message


def local_lodging_period_result(payload, response_format):
    if payload is None:
        return None
    step = payload.get("lodging_period_confirmation", {})
    if not isinstance(step, dict):
        return None
    replies = {
        "ask": PERIOD_QUESTION, "keep": KEEP_DAY_TRIP_REPLY, "date_required": DATE_REQUIRED_REPLY,
    }
    text = replies.get(step.get("action"))
    if text is None:
        return None
    reply = response_format.model_validate({
        "reply": text, "options": payload["options"], "plan": None,
    })
    return {
        "chat_reply": reply, "model_call_count": 0, "tool_call_counts": {},
        "route_calls": [], "messages": [], "place_detail_errors": [], "visit_order_errors": [],
    }