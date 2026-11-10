import json
import sys
from typing import Literal

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from ..config import LLM_MODEL, LLM_REASONING_EFFORT, get_openai_api_key
from ..tools.bus import get_bus_route_summary
from ..tools.driving import get_driving_time
from ..tools.location import find_location
from ..tools.places import get_place_details, search_places
from ..tools.walking import get_walking_route

SYSTEM_PROMPT = """너는 이동약자와 동반자의 조건을 고려하여 제주 여행 일정을 만드는 도우미다.

입력 해석:
- 확정한 출발지 좌표가 제공되면 그 좌표를 사용한다. 이름 검색으로 다른 좌표를 선택하지 않는다.
- 출발지 좌표가 없을 때만 find_location으로 조회한다. 좌표를 지어내지 않는다.
- 이동 보조기구가 미선택이면 사용 여부를 추정하지 않는다. 선택한 필수 이용 조건은 별도로 적용한다.
- 사용자가 정하지 않은 조건만 일반적인 값으로 정하고 assumptions에 적는다.
- 추가 요청이 선택한 시간이나 필수 조건과 충돌하면 조건을 임의로 완화하지 않는다.

여행지와 접근성:
- 여행지는 search_places 결과에서만 고른다.
- 일정에 넣기 전에 get_place_details로 출입과 관람 구간, 계단, 경사, 화장실 등의 정보를 확인한다.
- 필수 이용 조건을 충족하는 근거가 없는 장소는 확정 일정에 넣지 않는다.
- 이용할 수 있는 구간과 제한되는 구간을 구분한다. 일부 구간의 설명을 장소 전체에 적용하지 않는다.
- accessibility_notes에는 확인한 내용과 미확인 내용을 구분하고, 근거가 되는 지점 이름을 함께 적는다.
- 도보 경로의 존재나 저상버스 여부만으로 전체 동선의 접근성을 보장하지 않는다.

이동수단:
- 자동차, 렌터카, 택시는 get_driving_time으로 이동시간을 확인한다.
- 도보는 get_walking_route로 이동시간을 확인한다. 자동차나 버스로 임의 변경하지 않는다.
- 대중교통과 버스는 get_bus_route_summary를 사용한다. 총 이동시간에 이미 포함된 도보 시간을 중복 합산하지 않는다.
- 실시간 버스 정보는 조회 시점의 정보이며, 미래 탑승 시점의 저상버스 운행을 보장하지 않는다.
- 사용할 수 있는 이동시간을 얻지 못하면 임의의 시간으로 채우지 말고 후보나 동선을 조정한다.

시간과 휴식:
- 지정한 여행 가능 시간 안에 이동, 방문, 휴식을 모두 포함한다.
- 항목은 시간순으로 배치하고 서로 겹치지 않게 한다. 종료 시각과 시작 시각의 차이는 duration_minutes와 일치시킨다.
- 이동 도구의 초 단위 소요시간은 분 단위로 올림하고, 거리는 미터를 1,000으로 나누어 km로 변환한다.
- 중간 휴식이 지정되면 한 방문 장소의 체류시간에 해당 휴식을 1회 포함한다. 휴식 시간을 별도로 다시 더하지 않는다.
- 해당 visit의 accessibility_notes에 체류시간 중 휴식하는 시간을 적는다. 휴식 시설의 존재는 확인된 자료를 근거로 설명한다.
- 출발지로 돌아오기를 선택하면 마지막 귀환 이동도 조회하고 여행 종료 시각 안에 도착하도록 한다.
- 마지막 여행지에서 마치기를 선택하면 출발지 귀환을 필수로 넣지 않는다.

출력:
- 하루마다 방문(visit)과 이동(move) 항목을 시간순으로 넣는다.
- visit의 place_id는 search_places 결과를 그대로 쓴다. 휴식은 별도의 item_type으로 만들지 않는다.
- 조건을 충족하는 일정을 만들 수 없으면 해당 날짜의 items를 빈 목록으로 두고, 이유와 확인이 필요한 조건을 assumptions에 적는다.
"""

class ScheduleItem(BaseModel):
    start_time: str = Field(description="시작 시각, 예: 10:00")
    end_time: str = Field(description="끝 시각, 예: 10:20")
    item_type: Literal["visit", "move"] = Field(description="visit: 장소 방문, move: 장소 사이 이동")
    title: str = Field(description="visit이면 장소 이름, move이면 '출발지 → 도착지'")
    place_id: str | None = Field(default=None, description="visit일 때 search_places 결과의 place_id")
    transport: str | None = Field(default=None, description="move일 때 이동수단: 렌터카, 택시, 버스, 도보")
    duration_minutes: int | None = Field(default=None, description="머무는 시간 또는 이동 시간(분)")
    distance_km: float | None = Field(default=None, description="move일 때 이동 거리(km)")
    accessibility_notes: list[str] = Field(default_factory=list, description="주차, 경사·동선, 화장실, 대여 등 접근성 메모")

class DayPlan(BaseModel):
    day: int = Field(description="여행 N일차")
    items: list[ScheduleItem]

class TripPlan(BaseModel):
    assumptions: list[str] = Field(default_factory=list, description="사용자가 말하지 않아 임의로 정한 조건")
    days: list[DayPlan]

def build_planner_agent():
    model = ChatOpenAI(
        model=LLM_MODEL,
        api_key=get_openai_api_key(),
        use_responses_api=True,
        reasoning={"effort": LLM_REASONING_EFFORT},
        max_retries=2,
        timeout=120,
    )
    tools = [
        find_location,
        search_places,
        get_place_details,
        get_driving_time,
        get_bus_route_summary,
        get_walking_route,
    ]
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        response_format=TripPlan,
    )
    return agent

def count_tool_calls(messages: list) -> dict[str, int]:
    tool_call_counts = {}
    for message in messages:
        tool_calls = getattr(message, "tool_calls", None) or []
        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            previous_count = tool_call_counts.get(tool_name, 0)
            tool_call_counts[tool_name] = previous_count + 1
    return tool_call_counts

def count_model_calls(messages: list) -> int:
    model_call_count = 0
    for message in messages:
        if message.type == "ai":
            model_call_count += 1
    return model_call_count

def plan_trip(user_message: str) -> dict:
    """사용자 요청 글을 Agent에 넘겨 일정(TripPlan)과 호출 횟수를 돌려줍니다.
    일정이 형식에 맞게 오지 않으면 trip_plan은 None입니다."""
    agent = build_planner_agent()
    result = agent.invoke({"messages": [{"role": "user", "content": user_message}]})
    messages = result["messages"]

    return {
        "trip_plan": result.get("structured_response"),
        "model_call_count": count_model_calls(messages),
        "tool_call_counts": count_tool_calls(messages),
    }

def main():
    if len(sys.argv) < 2:
        print('사용법: python -m backend.app.agent.planner "요청 문장"')
        return

    user_message = sys.argv[1]
    result = plan_trip(user_message)

    trip_plan = result["trip_plan"]
    if trip_plan is None:
        print("일정을 형식에 맞게 받지 못했습니다.")
    else:
        print(json.dumps(trip_plan.model_dump(), ensure_ascii=False, indent=2))
    print()
    print("LLM 호출 횟수:", result["model_call_count"])
    print("tool 호출 횟수:", result["tool_call_counts"])

if __name__ == "__main__":
    main()
