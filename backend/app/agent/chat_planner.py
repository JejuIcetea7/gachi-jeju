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
from ..tools.visit_order import suggest_visit_order
from ..services.selected_lodging_location import bind_selected_lodging_location
from ..tools.places import (
    get_place_details,
    search_places,
    search_places_by_name,
)
from .planner import SYSTEM_PROMPT, count_model_calls, count_tool_calls
from ..services.chat_request_agent import (
    chat_model_http_client,
    guard_chat_tool,
)
from ..services.chat_request_control import (
    check_chat_request_control,
    get_chat_request_control,
    get_chat_request_timeout,
)
from ..services.chat_route_points import (
    bind_chat_route_tools, read_chat_route_payload,
    bind_current_place_details, validate_plan_place_details, validate_plan_visit_order,
)
from ..services.plan_evaluation import extract_tool_trace
from ..services.lodging_period import local_lodging_period_result

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

숙박과 기간 확인:
- 당일 조건에 숙박이 필요한 요청이 있으면 기간을 임의로 늘리지 말고 확인한다.
  호텔 위치·카페·식당·체크인 시간 같은 정보 문의만으로 숙박을 추가하지 않는다.
- lodging_period_confirmation.action이 accepted이면 사용자가 바로 앞의 기간 질문에 동의했다.
  options에는 확인한 기간이 반영돼 있다. original_request의 숙소 요청을 이어 처리하고
  같은 기간 질문을 반복하지 않는다. 새 숙소는 이번 실행에서 위치를 확인한다.
- action이 specified이면 사용자가 기간을 구체적으로 다시 지정한 답변이다.
  현재 message의 명시한 기간을 반영하고 original_request의 숙소 요청을 함께 처리한다.
- 기간 연장만으로 관광지·방문일·체류시간·보조기구·필수 시설 조건을 바꾸지 않는다.
  새 숙소를 지정하지 않았다면 임의로 선택하지 말고 숙소 미정으로 관광 일정을 구성한다.
  새로 생긴 날짜에 방문 요청이 없으면 관광지를 임의 추가하지 말고 이유를 설명한다.

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
- 여러 날 여행에서 숙소를 말하지 않았거나 아직 정하지 않았으면 lodging_status를 undecided로 두고
  관광 일정을 먼저 만든다. 숙소가 정해질 때까지 같은 숙소 질문을 반복하지 않는다.
  undecided는 후속 대화에서도 유지하고, 사용자가 숙소를 제공하거나 불필요하다고 말하면 변경한다.
- 당일 일정에서 사용자가 숙소를 요청하지 않았다면 숙소 질문이나 미정 메모를 reply와 assumptions에 넣지 않는다.
  숙박 불필요(not_needed)를 선택한 경우에도 숙소 미정 안내를 추가하지 않는다.
- options.lodging_stays에는 사용자가 선택하거나 제공한 숙박일별 숙소 위치가 들어 있다.
  현재 message에서 해당 숙소나 주소를 변경·취소하지 않았다면 전달된 night·name·address·위도·경도·provider_place_id·place_url을 유지한다.
  선택한 숙소의 위치를 다시 확인하기 위해 find_location을 호출하지 말고 전달된 좌표로 필요한 이동을 조회한다.
  find_location 응답의 source가 selected_lodging이면 선택 정보를 재사용한 것이며 새 위치 조회 근거가 아니다.
  다른 숙소·주소 정정·새 숙박일 추가에 새 위치 조회가 필요하고 도구에 refresh 인자가 제공된 경우 refresh=true로 조회한다.
  일부 숙박일만 선택했다면 다른 숙박일을 임의로 채우거나 lodging_status를 provided로 바꾸지 않는다.
  reply와 assumptions에는 선택한 숙소 위치를 반영해 이동과 시간을 계산했다는 점을 안내한다.
  선택 좌표로 이동을 조회한 경우 별도 이름 검색을 하지 않았다는 이유로 '숙소 위치를 확인하지 못했다'는 안내를 붙이지 않는다.
  선택 좌표 사용을 숙소의 현장 위치·영업·가격·잔여 객실·접근성을 별도로 검증한 것으로 설명하지 않는다.
- 사용자가 이름·주소로 새 숙소를 지정하거나 기존 숙소의 위치를 변경·정정하면 find_location으로 새 위치를 확인한다.
  기존 숙소 좌표를 새 숙소나 정정한 주소의 좌표로 재사용하지 않는다.
  확인한 이름·주소·좌표와 N번째 숙박일을 lodging_stays에 저장하고, 변경하지 않은 다른 숙박일 선택은 유지한다.
  한 숙소에 계속 머문다고 하면 각 숙박일에 같은 위치를 넣고, 숙소를 옮긴다면 숙박일별로 구분한다.
  새 위치의 이름이나 주소가 모호하거나 조회하지 못하면 해당 숙소의 위치만 질문한다. 검색 후보를 임의로 골라 확정하지 않는다.
  새 위치 조회의 find_location 응답에 없는 provider_place_id와 place_url은 null로 둔다.
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
  마지막 날에 관광지 방문 요청이 없어도 귀환 이동을 생략하지 않는다.
  전날 선택한 숙소와 원래 출발지가 확인돼 있으면 숙소→원래 출발지 이동을 조회한다.
  마지막 날에는 이 이동만 move로 넣을 수 있으며, 관광지를 임의로 추가하지 않는다.
- 숙소 선택이나 기간 변경 뒤에는 reply와 assumptions도 최종 숙소 상태에 맞게 다시 작성한다.
  모든 숙박일의 숙소가 선택돼 있으면 이전의 숙소 미정·미제공 안내를 재사용하지 않는다.
  숙소 위치 확인과 호텔의 현장 운영·객실·접근성 확인은 구분한다.
- 후속 수정은 기존 일정과 조건을 참고하고, 요청하지 않은 부분은 가능한 한 유지한다.
  수정 결과도 변경 부분만이 아닌 전체 일정으로 반환한다.

방문 순서와 동선 효율:
- 사용자가 장소를 나열한 순서만으로 방문 순서를 고정하지 않는다. 명시한 방문일·시각·순서는 우선 유지한다.
- 순서를 조정할 수 있는 여행지가 2곳 이상이면 suggest_visit_order로 직선거리 기준 순서 후보를 비교한다.
  이번 검색·상세 조회에서 확인한 place_ids만 전달하며 고정 시각·순서가 있는 장소는 fixed_place_ids로 보호한다.
  고정 장소 사이의 구간을 나눠 비교하고, 도구 결과를 이유로 고정 시각이나 지정한 방문일을 변경하지 않는다.
- 날짜가 미정인 장소는 가까운 권역끼리 같은 날에 묶고, 각 날짜의 실제 출발·도착 위치로 순서를 다시 비교한다.
  1일차 출발은 확인된 출발지, 다음 날 출발은 선택한 전날 숙소, 종료는 당일 숙소 또는 요청한 마지막 날 귀환지다.
  숙소가 미정인 경계는 좌표를 생략하며 센터·전날 관광지·가상의 숙소를 대신 넣지 않는다.
  여러 날의 일정은 권역을 날짜에 배치한 뒤 날짜별로 suggest_visit_order를 각각 호출한다.
  전체 기간을 원래 출발지에서 출발해 돌아오는 한 번의 순회로 계산하지 않는다.
  숙소 미정 1박 2일에 원래 출발지 귀환을 요청했다면 첫날은 출발 참조만, 둘째 날은 귀환 참조만 전달한다.
  12곳이 넘으면 고정된 경계를 유지한 작은 구간으로 나누고 전체 최적 경로를 계산했다고 설명하지 않는다.
- 순서 후보는 직선거리 비교이며 실제 자동차·도보·버스 이동시간이나 접근성의 최적해가 아니다.
  기존과 같은 이동수단으로 필요한 모든 구간을 조회하고 운영시간·휴무일·필수 접근 조건·체류시간을 지킨다.
  버스는 직선거리만으로 순서를 확정하지 말고 환승·도보와 조회된 총 이동시간을 함께 고려한다.
- 숙소 선택·변경 뒤에도 새 숙소의 출발·도착 위치를 반영해 조정 가능한 순서를 다시 비교한다.
  후속 수정에서 요청하지 않은 관광지·방문일·체류시간을 임의로 삭제하거나 변경하지 않는다.
- 실제 조회 경로로 조건을 충족하는 후보를 채택한다. 거리 비교만으로 최단시간이나 이용 가능을 보장하지 않는다.
  비교에 실패하거나 고정 조건 때문에 후보를 채택하지 못하면 확인된 기존 순서와 조건을 유지한다.

시설 정보 요약:
- 필수 시설은 장소별로 확인한 Y·N·unknown과 조사 원문의 충돌 여부를 구분해 설명한다.
- 일부 장소가 미확인이라는 이유로 모든 장소를 미확인이라고 요약하지 않는다.
  확인된 시설이 있는 장소와 현장 확인이 필요한 장소를 나누어 안내한다.
- 필수 시설의 집계값을 설명할 때는 장소명과 Y·N·unknown을 장소별로 적는다.
  상세 조사 지점에 시설 기록이 있다는 사실과 집계값이 Y라는 판단을 혼동하지 않는다.

휴무일:
- 일정에 넣을 장소는 get_place_details의 visit_dates에 여행 날짜(start_date부터 여행 일수만큼, YYYY-MM-DD)를 넣어 확인한다.
- closed_day_checks에서 그 일차 날짜가 '휴무'인 장소는 그 일차에 넣지 않는다.
- '확인 안 됨'인 장소는 넣을 수 있고, 해당 visit의 accessibility_notes에 '휴무일 확인 필요'를 넣는다.

운영시간·관람료:
- get_place_details의 opening_hours에서 방문 날짜의 달에 맞는 개장·폐장·입장 마감 시각을 확인한다.
- 방문 시작은 개장 시각 이상, 입장 마감이 있으면 그 시각 이하로 잡고, 방문 종료는 폐장 시각 이하로 잡는다.
  개장·입장 마감·폐장 정각은 허용한다. 입장 마감이 없으면 임의의 시각을 만들지 않는다.
- 방문 불가 사유는 실제로 위반한 조건만 reply와 assumptions에 적는다.
  입장 마감은 방문 시작 시각과, 폐장은 방문 종료 시각과 각각 비교한다.
- 예를 들어 입장 마감 17:30, 폐장 18:00인 장소의 17:40~17:55 방문은 입장 마감 위반만 설명한다.
  종료 시각이 폐장 전이면 폐장 시각을 넘기거나 폐장 전 관람을 완료할 수 없다고 덧붙이지 않는다.
- opening_hours가 '확인 안 됨'인 장소는 넣을 수 있고, 해당 visit의 accessibility_notes에 '운영시간 확인 필요'를 넣는다.
- 관람료는 admission_fee를 근거로 안내하고, '확인 안 됨'이면 금액을 지어내지 않는다.

요청별 위치 참조:
- 이동 도구에 origin_ref·destination_ref 인자가 있으면 좌표를 직접 전달하지 않는다.
  DB 관광지는 place:P001처럼 place:접두사와 place_id를 사용한다.
  현재 options에서 좌표까지 확인한 원래 출발지와 요청한 최종 귀환지는 departure다.
  선택한 N번째 숙박일의 숙소는 stay:N이다. 미정 숙소의 참조를 만들지 않는다.
- 출발지가 미정이거나 새 출발지·숙소로 변경하면 find_location으로 조회하고,
  그 응답의 point_ref(found:...)를 해당 새 위치의 이동에 사용한다.
  이름이 같거나 가까운 장소라도 다른 place_id의 좌표나 참조를 대신 쓰지 않는다.
- suggest_visit_order에 day 인자가 있으면 해당 일차의 장소만 place_ids에 넣는다.
  경계도 origin_ref·destination_ref로 지정하며 미정인 경계는 null로 둔다.
  귀환을 요청한 숙소 미정 1박 2일은 1일차 origin_ref=departure, destination_ref=null,
  2일차 origin_ref=null, destination_ref=departure다.
  출발지를 새로 조회했다면 departure 대신 이번 조회의 point_ref를 사용한다.
- 이동 응답의 origin·destination에 표시된 장소명과 방향을 확인한 뒤 해당 move에 route_id를 복사한다.
  방문 순서를 바꾸면 새 순서의 출발→도착 방향으로 이동을 다시 조회한다.
- 이전 일정의 관광지를 유지하는 숙소 선택·시간 수정 요청도 이번 생성 시도에서
  모든 방문 장소의 get_place_details를 먼저 호출하고 방문 날짜를 확인한다.
  장소 ID로 좌표를 읽는 것과 이전 답변의 상세 설명은 이번 상세 조회를 대신하지 않는다.
- details_required가 나오면 place_ids의 상세 정보를 조회한 뒤 이동·순서 비교를 다시 요청한다.
  자동 재생성에서도 첫 시도의 상세 조회를 재사용하지 않는다.
- stay:N은 요청 options에 전달된 기존 숙소의 위치이며 새 숙소로 자동 변경되지 않는다.
  숙소를 변경했다면 find_location 응답의 새 point_ref를 이동 도구뿐 아니라
  suggest_visit_order의 해당 숙박일 도착과 다음 날 출발에도 사용한다.
  새 숙소를 options에 저장한 뒤 기존 stay:N으로 순서를 비교한 결과를 근거로 삼지 않는다.

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
- 버스 이동 정거장 수는 segments의 ride_stop_count를 그대로 안내한다. null이면 미확인으로 설명한다.
  arriving_buses의 remaining_stops는 현재 차량의 도착 정보이며 이용자가 이동할 정거장 수로 사용하지 않는다.

검증 실패 후 자동 재생성:
- 마지막 사용자 JSON에 plan_retry가 있으면 같은 요청의 검증 오류를 수정하는 단계다.
- plan_retry.locked_options에 들어 있는 조건을 응답 options에 유지한다.
  lodging_recheck_nights가 있으면 해당 숙박일의 숙소와 숙소 상태는 재확인 대상이다.
  첫 시도에서 거절된 숙소를 확정한 것으로 취급하지 않는다.
  해당 숙박일은 원래 options의 선택을 유지하거나 이번 실행에서 확인한 사용자 지목 숙소로 수정한다.
  새 숙소의 정확한 이름·주소를 확인할 수 없으면 원래 선택을 유지하고 plan=null로 질문한다.
  재확인 대상 이외의 숙박일과 다른 여행 조건은 변경하지 않는다.
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
- reply와 assumptions에 시각을 안내하면 최종 plan 항목의 시각을 그대로 사용한다.
  출발지에서 이동을 시작하는 시각과 방문지에서 방문을 시작하는 시각을 구분한다.
  방문 시작을 안내할 때는 해당 visit의 start_time을 사용한다.
  귀환 시각은 귀환 move의 end_time을 기준으로 하며 이동시간을 별도로 다시 더하지 않는다.
  여러 날의 귀환 시각을 설명할 때는 해당 일차를 함께 적는다.
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
    http_client=None,
    location_tool=None,
    request_payload=None,
):
    check_chat_request_control()
    control = get_chat_request_control()
    if control is not None and http_client is None:
        raise ValueError("제어된 대화 요청에는 전용 모델 HTTP 클라이언트가 필요합니다.")
    if route_calls is None:
        route_calls = []

    location = find_location if location_tool is None else location_tool
    order = suggest_visit_order
    details = get_place_details
    route_tools = [
        create_driving_map_tool(route_calls),
        create_bus_map_tool(route_calls),
        create_walking_map_tool(route_calls),
    ]
    if request_payload is not None:
        details, require_details = bind_current_place_details(get_place_details)
        location, order, route_tools = bind_chat_route_tools(
            request_payload, location_tool=location, order_tool=order,
            driving_tool=route_tools[0], bus_tool=route_tools[1],
            walking_tool=route_tools[2], require_details=require_details,
        )
    tools = [
        location, search_places, search_places_by_name, order,
        details, details, *route_tools,
    ]
    model_options = {}
    if http_client is not None:
        model_options["http_client"] = http_client

    model = ChatOpenAI(
        model=LLM_MODEL,
        api_key=get_openai_api_key(),
        use_responses_api=True,
        reasoning={"effort": LLM_REASONING_EFFORT},
        max_retries=2,
        timeout=get_chat_request_timeout(120),
        **model_options,
    )
    return create_agent(
        model=model,
        tools=[guard_chat_tool(function, control) for function in tools],
        system_prompt=CHAT_SYSTEM_PROMPT,
        response_format=response_format,
    )


def run_chat_agent(
    messages: list[dict[str, str]],
    response_format: type[BaseModel],
) -> dict:
    """답변과 이번 실행의 호출 횟수·지도 경로 기록을 반환합니다."""
    check_chat_request_control()
    request_payload = read_chat_route_payload(messages)
    local_result = local_lodging_period_result(request_payload, response_format)
    if local_result is not None:
        check_chat_request_control()
        return local_result
    route_calls = []
    try:
        with chat_model_http_client() as http_client:
            arguments = {"route_calls": route_calls}
            if request_payload is not None:
                arguments["request_payload"] = request_payload
            location_tool = bind_selected_lodging_location(find_location, messages)
            if location_tool is not find_location:
                arguments["location_tool"] = location_tool
            if http_client is not None:
                arguments["http_client"] = http_client
            agent = build_chat_agent(response_format, **arguments)
            result = agent.invoke({"messages": messages})
    finally:
        check_chat_request_control()

    new_messages = result["messages"][len(messages):]
    generated_reply = result.get("structured_response")
    generated_plan = getattr(generated_reply, "plan", None)
    plan_data = generated_plan.model_dump() if generated_plan is not None else None
    tool_trace = extract_tool_trace(new_messages) if generated_plan is not None else []
    detail_errors = validate_plan_place_details(plan_data, tool_trace)
    order_errors = validate_plan_visit_order(
        plan_data,
        generated_reply.options.model_dump() if generated_plan is not None else {},
        tool_trace,
    )
    return {
        "chat_reply": result.get("structured_response"),
        "model_call_count": count_model_calls(new_messages),
        "tool_call_counts": count_tool_calls(new_messages),
        "route_calls": route_calls,
        "messages": new_messages,
        "place_detail_errors": detail_errors,
        "visit_order_errors": order_errors,
    }