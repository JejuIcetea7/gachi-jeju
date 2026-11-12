from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..config import LLM_MODEL, LLM_REASONING_EFFORT, get_openai_api_key
from ..tools.map_transport import (
    create_driving_map_tool,
    create_walking_map_tool,
    create_bus_map_tool,
)
from ..tools.location import find_location
from ..tools.places import get_place_details, search_places
from .planner import SYSTEM_PROMPT, count_model_calls, count_tool_calls


CHAT_SYSTEM_PROMPT = SYSTEM_PROMPT + """
대화 모드:
- 마지막 사용자 메시지는 JSON이다. message가 이번 요청이고, options는 현재 선택 조건,
  current_plan은 이전 일정, reference_date는 한국 기준 오늘 날짜다.
- 앞선 사용자·도우미 대화도 참고하여 생략된 말과 후속 수정 요청을 해석한다.
- current_plan은 참고 자료이며 장소·좌표·접근성의 검증 근거로 사용하지 않는다.
- 출발지를 정하지 않았다면 default_departure의 센터를 기본 출발지로 안내한다.
- 방문하고 싶은 장소와 출발지를 혼동하지 않는다.
- 출발지를 변경하면 이전 장소의 좌표를 재사용하지 않는다.
  새 좌표는 find_location으로 확인하고, 확인되지 않으면 위도·경도를 모두 null로 둔다.
- 이미 확인한 조건을 반복해서 묻지 않는다.
- 사용자가 명확히 바꾼 조건은 options에도 반영한다.
  변경 의도가 불분명하거나 필수 접근 조건을 완화하는 요청은 먼저 확인한다.
- 보조기구 사용 여부는 사용자가 말했거나 선택한 경우에만 반영한다.

질문과 일정 생성:
- 날짜·여행 일수·이동수단이 없으면 임의로 정하지 말고 필요한 항목을 간단히 질문한다.
- 일반적인 장소 문의에는 도구로 확인한 내용을 답하고 날짜 입력을 강요하지 않는다.
- 여행 종류가 미선택이어도 일정을 추천할 수 있다. 별도 취향이 없으면 전체 분류에서 찾는다.
- 당일은 1일, 1박 2일은 2일, 2박 3일은 3일이다.
  현재는 최대 3일을 지원하며 더 긴 요청을 임의로 줄이지 않는다.
- '내일' 같은 날짜는 reference_date를 기준으로 해석한다.
- 시작·종료 시각이나 휴식을 사용자가 요청하면 일정에 반영한다.
  시각을 정하지 않았다면 활동 시간을 제안하고 assumptions에 적는다.
- 여러 날의 일정은 1일차부터 요청한 일수까지 구분한다.
  숙소 위치가 필요하면 질문하고, 미확정 숙소의 위치나 이동시간을 지어내지 않는다.
- 여러 날의 일정에서 출발지 귀환을 선택하면 마지막 날의 귀환을 반영한다.
- 후속 수정은 기존 일정과 조건을 참고하고, 요청하지 않은 부분은 가능한 한 유지한다.
  수정 결과도 변경 부분만이 아닌 전체 일정으로 반환한다.

휴무일:
- 일정에 넣을 장소는 get_place_details의 visit_dates에 여행 날짜(start_date부터 여행 일수만큼, YYYY-MM-DD)를 넣어 확인한다.
- closed_day_checks에서 그 일차 날짜가 '휴무'인 장소는 그 일차에 넣지 않는다.
- '확인 안 됨'인 장소는 넣을 수 있고, 해당 visit의 accessibility_notes에 '휴무일 확인 필요'를 넣는다.

지도 경로 연결:
- 이동 도구가 route_id를 반환하면 해당 값을 대응하는 move의 route_id에 그대로 넣는다.
- route_id를 받지 못한 이동과 visit의 route_id는 null로 둔다.
- route_id는 이번 실행의 도구 응답에서만 가져온다. 이전 일정의 값을 재사용하거나 새로 만들지 않는다.
- 자동차·도보·대중교통 일정을 생성하거나 수정할 때는 유지하는 이동 구간도 이번 실행에서 다시 조회한다.
- 하나의 route_id는 하나의 move 항목에만 사용한다.
- get_bus_route_summary의 route_id는 환승과 도보를 포함한 출발지부터 도착지까지의 전체 경로다.
  이 조회 결과는 move 하나로 작성하고, 구간별 move로 나누거나 같은 route_id를 재사용하지 않는다.

대화 응답 형식:
- reply에는 사용자에게 보여 줄 간단한 답변, 질문 또는 변경 요약을 쓴다.
- options에는 이번 요청까지 반영한 현재 조건 전체를 넣는다.
  알 수 없는 값은 null, 선택하지 않은 목록은 빈 배열로 둔다.
- 정보 확인 질문이나 일반 답변이면 plan은 null이다.
- 일정을 생성·수정했다면 plan에 TripPlan 형식의 전체 일정을 넣는다.
- 조건에 맞는 일정을 만들 수 없는 경우에는 해당 일차의 items를 빈 배열로 두고
  assumptions와 reply에 이유를 설명한다.
- 조회하지 않은 사실이나 지도를 그릴 경로 좌표를 만들어내지 않는다.
"""

def build_chat_agent(
    response_format: type[BaseModel],
    *,
    route_calls: list[dict] | None = None,
):
    if route_calls is None:
        route_calls = []

    driving_tool = create_driving_map_tool(route_calls)
    walking_tool = create_walking_map_tool(route_calls)
    bus_tool = create_bus_map_tool(route_calls)

    model = ChatOpenAI(
        model=LLM_MODEL,
        api_key=get_openai_api_key(),
        use_responses_api=True,
        reasoning={"effort": LLM_REASONING_EFFORT},
        max_retries=2,
        timeout=120,
    )
    return create_agent(
        model=model,
        tools=[
            find_location,
            search_places,
            get_place_details,
            driving_tool,
            bus_tool,
            walking_tool,
        ],
        system_prompt=CHAT_SYSTEM_PROMPT,
        response_format=response_format,
    )

def run_chat_agent(
    messages: list[dict[str, str]],
    response_format: type[BaseModel],
) -> dict:
    """답변과 이번 실행의 호출 횟수·지도 경로 기록을 반환합니다."""
    route_calls = []
    agent = build_chat_agent(
        response_format,
        route_calls=route_calls,
    )
    result = agent.invoke({"messages": messages})

    new_messages = result["messages"][len(messages):]
    return {
        "chat_reply": result.get("structured_response"),
        "model_call_count": count_model_calls(new_messages),
        "tool_call_counts": count_tool_calls(new_messages),
        "route_calls": route_calls,
        "messages": new_messages,
    }