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
from ..tools.places import (
    get_place_details,
    search_places,
    search_places_by_name,
)
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
- 여행 기간에는 별도의 일수 상한을 두지 않는다.
  출발일부터 마지막 날까지 양 끝 날짜를 포함해 여행 일수를 계산한다.
  당일은 1일이고, N박은 N+1일이다. 요청한 기간을 임의로 줄이지 않는다.
  날짜와 여행 일수가 서로 맞지 않으면 사용자에게 확인한다.
- '내일' 같은 날짜는 reference_date를 기준으로 해석한다.
- 시작·종료 시각이나 휴식을 사용자가 요청하면 일정에 반영한다.
  시각을 정하지 않았다면 활동 시간을 제안하고 assumptions에 적는다.
- 여러 날의 일정은 1일차부터 요청한 일수까지 구분한다.
  숙소 미정은 관광 일정 생성의 필수 조건 누락으로 취급하지 않는다.
- 숙소를 말하지 않았거나 아직 정하지 않았으면 lodging_status를 undecided로 두고
  관광 일정을 먼저 만든다. 숙소가 정해질 때까지 같은 숙소 질문을 반복하지 않는다.
  undecided는 후속 대화에서도 유지하고, 사용자가 숙소를 제공하거나 불필요하다고 말하면 변경한다.
- 이미 정한 숙소를 사용자가 제공하면 lodging_status는 provided이며, find_location으로 위치를 확인한다.
  확인한 이름·주소·좌표와 N번째 숙박일을 lodging_stays에 저장한다.
  한 숙소에 계속 머문다고 하면 각 숙박일에 같은 위치를 넣고, 숙소를 옮긴다면 숙박일별로 구분한다.
  이름이나 위치가 모호하면 해당 숙소의 위치만 확인한다. 검색 후보를 임의로 골라 확정하지 않는다.
  find_location 응답에 없는 provider_place_id와 place_url은 null로 둔다.
  숙소 추천 후보는 관광 일정 생성 후 별도로 조회하므로 조회하지 않은 숙소 이름·가격·접근성은 지어내지 않는다.
- lodging_stays의 night=N은 N일차 관광 후 머무는 숙소다.
  선택한 숙소까지의 이동은 N일차 마지막 move, 다음 날 숙소 출발은 N+1일차 첫 move로 넣는다.
  숙소를 관광지 visit으로 만들거나 외부 검색 식별값을 DB place_id로 사용하지 않는다.
- 선택하지 않은 숙박일은 그날 마지막 관광지에서 일정을 끝내고 다음 날 첫 관광지에서 시작한다.
  미정 숙소 위치·숙소 이동시간·전날 마지막 관광지를 다음 날 숙소 출발지로 지어내지 않는다.
  이 구간을 제외한 관광 일정이라는 점과 숙소 선택 후 시간 재계산이 필요하다는 점을 assumptions와 reply에 적는다.
- 숙소 미정인 1박 2일의 예: 1일차는 원래 출발지→첫 관광지부터 마지막 관광지까지,
  2일차는 첫 관광지 방문부터 시작하며 마지막 날 귀환을 요청했으면 관광지→원래 출발지를 넣는다.
  2일차 아침에 센터·전날 관광지·가상의 숙소에서 첫 관광지로 가는 이동을 추가하지 않는다.
- 매일 원래 출발지에서 출발·귀환해 숙박이 필요 없다고 사용자가 명시하면 lodging_status는 not_needed다.
  숙박 불필요를 사용자가 명시하지 않았는데 원래 출발지를 매일의 숙소로 간주하지 않는다.
- 여러 날의 일정에서 출발지 귀환을 선택하면 마지막 날의 귀환을 반영한다.
- 후속 수정은 기존 일정과 조건을 참고하고, 요청하지 않은 부분은 가능한 한 유지한다.
  수정 결과도 변경 부분만이 아닌 전체 일정으로 반환한다.

휴무일:
- 일정에 넣을 장소는 get_place_details의 visit_dates에 여행 날짜(start_date부터 여행 일수만큼, YYYY-MM-DD)를 넣어 확인한다.
- closed_day_checks에서 그 일차 날짜가 '휴무'인 장소는 그 일차에 넣지 않는다.
- '확인 안 됨'인 장소는 넣을 수 있고, 해당 visit의 accessibility_notes에 '휴무일 확인 필요'를 넣는다.

운영시간·관람료:
- get_place_details의 opening_hours로 방문 일정의 종료 시각이 그날 닫는 시각을 넘지 않게 한다.
  계절별 값은 그 일차 날짜의 달에 맞는 시간을 쓴다. 입장마감이 있으면 그 전에 도착한다.
- opening_hours가 '확인 안 됨'인 장소는 넣을 수 있고, 해당 visit의 accessibility_notes에 '운영시간 확인 필요'를 넣는다.
- 관람료는 admission_fee를 근거로 안내하고, '확인 안 됨'이면 금액을 지어내지 않는다.

지도 경로 연결:
- move의 transport에는 '자동차', '버스', '도보' 같은 일반 이동수단명만 쓴다.
  노선 번호와 승하차 정류장 설명을 transport에 섞지 않는다. 버스 구간 안내는 조회 결과로 연결한다.
- 이동 도구가 route_id를 반환하면 해당 값을 대응하는 move의 route_id에 그대로 넣는다.
- route_id를 받지 못한 이동과 visit의 route_id는 null로 둔다.
- route_id는 이번 실행의 도구 응답에서만 가져온다. 이전 일정의 값을 재사용하거나 새로 만들지 않는다.
- 자동차·도보·대중교통 일정을 생성하거나 수정할 때는 유지하는 이동 구간도 이번 실행에서 다시 조회한다.
- 하나의 route_id는 하나의 move 항목에만 사용한다.
- get_bus_route_summary의 route_id는 환승과 도보를 포함한 출발지부터 도착지까지의 전체 경로다.
  이 조회 결과는 move 하나로 작성하고, 구간별 move로 나누거나 같은 route_id를 재사용하지 않는다.

검증 실패 후 자동 재생성:
- 마지막 사용자 JSON에 plan_retry가 있으면 같은 요청의 검증 오류를 수정하는 단계다.
- plan_retry.locked_options를 응답 options에 그대로 유지한다.
  날짜·여행 기간·출발지·이동수단·보조기구·필수 조건을 임의로 변경하지 않는다.
- 원래 message에 있는 시간과 추가 요청도 유지한다.
  휴무일 문제를 해결하려고 여행 날짜나 필수 조건을 바꾸지 않는다.
- plan_retry.validation_errors를 참고해 오류를 수정하고 전체 일정을 다시 만든다.
- plan_retry.failed_plan은 실패한 시도의 참고 자료이며 검증 근거가 아니다.
- 장소 검색·상세 조회와 필요한 이동 조회를 이번 실행에서 다시 수행한다.
  실패한 시도의 경로 ID와 도구 결과를 재사용하지 않는다.
- 조건을 충족할 수 없다면 plan을 null로 두고 필요한 확인 사항을 reply로 질문한다.

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
            search_places_by_name,
            get_place_details,
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
