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

SYSTEM_PROMPT = """너는 휠체어·유모차 이용자 같은 이동 약자를 위한 제주도 여행 일정 도우미다.

규칙:
- 출발지 좌표는 find_location으로 구한다. 좌표를 지어내지 않는다.
- 여행지는 반드시 search_places 결과에서만 고른다. 결과에 없는 장소는 넣지 않는다.
- 일정에 넣기 전에 get_place_details로 경사, 계단, 화장실, 대여 정보를 확인한다.
- 장소 사이 이동시간은 반드시 이동 tool 결과를 쓴다. 렌터카·택시는 get_driving_time, 버스는 get_bus_route_summary, 가까운 거리 도보는 get_walking_route를 쓴다.
- 접근성 정보가 없거나 불확실하면 accessibility_notes에 그렇다고 적는다.
- 사용자가 말하지 않은 조건(출발 시각 등)은 일반적인 값으로 정하고 assumptions에 적는다.

일정 작성:
- 하루마다 방문(visit)과 이동(move) 항목을 시간 순서대로 넣는다.
- visit 항목의 place_id는 search_places 결과의 place_id를 그대로 쓴다.
- move 항목의 duration_minutes, distance_km는 이동 tool 결과를 분, km로 바꿔 쓴다.
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
