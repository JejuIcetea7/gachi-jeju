# 단일 에이전트 검증 흐름 전환 인수인계

2026-11-19 사용량 제약으로 작업을 중단한 시점의 기록이다. 구현은 진행 중이며 전체 테스트가 통과한 상태가 아니다.

## 1. 사용자가 승인한 방향

사용자는 다음 원칙으로 실제 수정을 요청했다.

- 문맥과 의도는 현재 LLM이 판단한다.
- 서버에서 정규식·특정 단어로 사용자의 추가·삭제·유지·변경·동의·거절 의도를 다시 해석하는 처리를 제거한다.
- 서버는 구조화된 값의 형식, 필수 필드, 실제 조건 준수, 계산과 조회 근거를 검증한다.
- 서버가 모델 답변을 고정 문장으로 교체하거나, 문장을 잘라내거나, 일정 시각을 자동으로 고쳐 통과시키는 처리를 제거한다.
- 검증 실패는 기대값·실제값·근거를 현재 에이전트에 돌려줘 수정하게 한다.
- 매 요청의 의도 분류를 위한 별도 모델 호출이나 새 에이전트를 추가하지 않는다.
- 현재 단일 에이전트의 도구 사용 흐름을 유지한다.
- 모델 설정은 gpt-6-luna, reasoning effort는 high다.
- 사용자에게 확인할 필요가 있으면 LLM이 문맥에 맞는 확인 질문을 작성한다.
- 재시도 횟수와 전체 시간 제한은 코드로 보장한다.

사용자가 이전에 휴식 요청을 따로 고치지 말라고 지시했다. 이번에는 전체 검증 흐름을 전환하는 과정에서 기존 휴식 숫자 검증도 구조화된 조건으로 받도록 이관했다. 별도의 휴식 기능 개선을 추가로 확장하지 않는다.

## 2. 저장소와 주의사항

- 프로젝트는 /Users/day/projects/jeju-codex다.
- 브랜치는 chore/agent-workflow-review다.
- HEAD는 0d1e67d지만 이 작업 이전부터 미커밋 변경이 많이 있었다.
- AGENTS.md 수정과 한국어 PDF 파일은 사용자 측 기존 작업이다.
- git restore나 reset으로 변경 파일 전체를 되돌리면 이전 작업까지 사라질 수 있다.
- 이번 전환 작업은 커밋하지 않았다. 빌드·전체 테스트 완료나 서비스 사용 준비 상태로 취급하면 안 된다.
- 보고서 0011과 0012는 이번 전환 이전의 결과다. 현재 변경 중인 코드의 검증 결과와 구분한다.

## 3. 변경 전 문제와 근거

직전 종합 점검에서는 다음 결함을 실제 실행으로 확인했다.

1. “어떤 도움을 줄 수 있어?”에 모델이 정상 서비스 소개를 작성했지만, 서버가 접근성 관련 단어와 “있어요” 등을 찾아 시설 미확인 안내로 바꿨다.
2. “박물관 소개해줘. 일정은 만들지 마”에 모델이 정상 소개를 작성했지만 keep 행동 때문에 서버가 일정 유지 문장으로 바꿨다.
3. “센터로 돌아오는 당일 일정”에서 모델이 귀환을 이해했지만, 서버의 표현 매칭이 이를 놓쳐 기존 false 조건 위반으로 거부했다.
4. 숙소·시간·날짜 변경에도 원문을 다시 해석하는 규칙과 강제 답변 처리가 여러 곳에 흩어져 있었다.
5. 당일 요청은 별도의 숙박 의도 분류 모델 호출을 거치고, 짧은 동의·거절은 서버가 어휘 목록으로 해석했다.

직전 보고서는 docs/experiments/0012-luna-high-comprehensive-audit.md다.

## 4. 현재 수정한 서비스 코드

### backend/app/agent/chat_planner.py

- run_chat_agent에서 별도 숙박 의도 분류 호출을 제거했다.
- 로컬 동의·거절 판정으로 LLM 호출을 생략하던 흐름도 제거했다.
- 기존 build_chat_agent와 도구 목록을 통해 실제 요청을 바로 처리한다.
- 중복 등록돼 있던 get_place_details를 한 번만 등록하도록 수정했다.
- 프롬프트는 changed_options, schedule_changes, visit_orders, needs_clarification, period_confirmed를 설명하도록 일부 수정했다.
- 의미와 문맥을 에이전트가 해석하고 서버가 문장을 교체하지 않는다고 명시했다.
- 최신으로 추가한 findings·available·restaurant_retry_request의 설명과 예시는 아직 충분히 반영하지 못했다.

### backend/app/routers/chat.py

ChatAgentReply에 현재 다음 필드가 있다.

- changed_options는 설정 전용 응답뿐 아니라 일정 생성·수정 때도 변경한 options 필드를 선언하는 용도로 바꿨다.
- schedule_changes는 방문 추가·삭제·이동·시간·체류 조건을 구조화한 목록이다.
- visit_orders는 사용자가 명시한 방문 순서다.
- replace_visits는 관광지 전체 교체 요청 여부다.
- needs_clarification은 사용자에게 정보·충돌 확인이 필요한지 표시한다.
- period_confirmed는 기간 연장을 사용자가 명시하거나 동의했는지 표시한다.
- restaurant_retry_request는 실제 일시적 식당 조회 오류에서 재시도할 원문이다.
- trip_date_change는 제거하고 날짜 변경도 changed_options와 options로 전달한다.
- AccessibilityCheck에 findings를 추가했다. topic마다 Y·N·unknown을 기록한다.
- VisitTimeCheck에 available을 추가했다. 운영정보로 방문 가능하면 true, 불가능하면 false, 미확인이면 null이다.

build_chat_messages는 대화와 조건을 전달한다. 숙박 동의·거절을 해석해 기간을 미리 변경하는 전처리는 제거했다.
기존 식당에는 이번 실행의 위치 참조와 별개로 schedule_key를 모델 입력에 추가한다. UI 응답용 모델을 변경한 것은 아니다.

validate_chat_reply는 다음을 검사한다.

- 행동·일정 변경·순서·재시도 요청의 근거가 실제 사용자 발화에 포함되는지.
- 선언하지 않은 기존 options 값이 바뀌었는지.
- 일정 변경을 선언했는데 plan이 없고 확인 질문도 아닌지.
- 확인 질문과 확정 일정을 동시에 반환하는지.
- 당일 기간을 연장하면서 period_confirmed가 누락됐는지.

create_chat은 기존 MAX_PLAN_RETRIES 안에서 위 검사와 일정 검증을 수행한다.
실패하면 build_plan_retry_messages로 같은 Agent에 오류를 전달한다.
첫 해석의 조건을 보존하는 locked_options·locked_changes·locked_orders·locked_replace를 넣었다.

_build_chat_result는 모델 reply를 유지하고 최종 일정의 실제 값을 검사하는 방향으로 수정했다.

- 접근성 관련 단어를 찾아 답변을 교체하는 처리를 제거했다.
- keep/optional만 있으면 무조건 고정 문장으로 바꾸는 처리를 제거했다.
- 모델이 만든 reply의 시간 문장을 제거하거나 타임라인으로 교체하는 처리를 제거했다.
- 식사 시각을 원문 정규식으로 읽어 강제로 바꾸는 처리를 제거했다.
- 미정 숙소 구간을 자동 삭제하던 전처리도 제거했다. 현재 경계 검증에서 잘못된 이동을 거부한다.
- 시설 findings를 실제 DB 상태와 비교하고, 잘못된 값은 피드백한다.
- 방문 가능 available을 운영시간·입장마감·폐장·휴무와 대조하고, 잘못된 값은 피드백한다.
- 조회 실패의 status가 api_error 또는 rate_limited이면 재시도 데이터의 존재를 검사한다. 답변 문장은 모델이 작성한다.
- verify_places, 경로·좌표 대조, 운영시간·휴무일, 접근성 필수 조건 검사는 재사용한다.

### backend/app/services/plan_validation.py

사용자 문장을 의도로 해석하던 큰 정규식 파서를 제거했다.

- apply_requested_meal_window를 제거했다.
- read_fixed_trip_options의 인자와 역할을 변경했다.
  현재는 기존 options, 모델이 제안한 options, 선언한 변경 필드를 받는다.
  지정 변경 필드만 반영하며 기존 값이 None인 필드는 제안 값으로 채울 수 있게 해 둔 상태다.
- read_schedule_constraints는 원문 문자열 대신 ScheduleChange 목록을 받도록 바꿨다.
- ScheduleChange와 VisitOrder를 추가했다. Pydantic 모델이다.
- 기존 ScheduleConstraints와 validate_schedule_constraints를 재사용한다.
- 기존 schedule_limits와 새 변경 조건을 병합한다.
- 방문 추가·삭제·일차 변경·체류시간·식사 개수·방문 순서를 실제 plan과 대조한다.

현재 ScheduleChange의 주요 값은 다음과 같다.

- visit은 DB ID, 기존 식당 schedule_key, 식사 전체 @meal, 방문 전체 @visits 또는 하루 경계 null이다.
- kind는 add, remove, move, start, end, earliest, latest, duration, minimum, maximum, rest, release다.
- minutes는 시각이면 자정 이후 분, 길이면 분이다.
- destination_day, count, category, evidence도 사용한다.

### 나머지 파일

- backend/app/services/plan_accessibility.py에서 ACCESSIBILITY_TERMS와 has_unchecked_accessibility_answer를 제거했다. 실제 시설 값 검증은 유지했다.
- backend/app/services/lodging_planning.py에서 지역 이름·별칭·사용자 문장 기반 의도 검사와 remaining_lodging_stays를 제거했다.
  새 숙소의 이름·주소·좌표를 현재 조회 결과와 비교하는 검증을 사용한다.
- backend/app/services/lodging_period.py를 삭제했다. 서버의 숙박 동의·거절·명시적 기간 판정과 별도 분류용 모델 정의가 들어 있던 파일이다.
- backend/app/services/plan_retry.py는 failed_plan=None인 일반 답변 검증 실패도 피드백할 수 있도록 변경했다.
- plan_reply_consistency.py의 자연어 검사 함수는 과거 평가 도구에서 사용한다. 현재 /chat 응답 경로에서는 호출하지 않는다. 평가용 검사와 서버 실행 경로를 구분해 판단한다.

## 5. 현재 실제 실행 결과

로컬 outputs/chat_eval/native-loop-20261119에 기록했다. 아래 결과는 개발 중간 시점의 코드로 실행한 것이며, 이후 추가 수정까지 검증한 결과는 아니다.

| 사례 | 결과 |
|---|---|
| B01 인사·도움 소개 | 200, 모델의 정상 서비스 소개 보존 |
| B04 박물관 소개, 일정 생성 제외 | 200, 조회한 소개 답변 보존 |
| N01 빈 상태에서 귀환 포함 당일 일정 | 200, 새 전체 일정 생성 |
| C11 여행일을 하루 미루고 숙소·관광 유지 | 200, 새 전체 일정 생성 |
| C10 박물관 90분 유지와 12~13시 점심 | 200, 조건 충돌 확인 질문 |

C10의 확인 질문은 “이번 실제 센터→박물관 이동이 30분이므로 10시 출발·90분 관람 후 식당 이동 2분을 더하면 12시 식사를 지킬 수 없다”는 내용이다.
서버가 거부한 상황이나 단순 미수행으로 곧바로 분류하지 말고, 기록된 실제 경로 시간과 대조해 판단해야 한다.
이 사례를 무조건 일정 생성 성공으로 만들기 위해 이동시간이나 식사 시각을 강제로 바꾸면 안 된다.

N01에서는 날짜·이동수단·귀환을 changed_options로 선언하고 시작 10시·박물관 90분을 schedule_changes로 반환했다.

## 6. 테스트 상태

마지막으로 완료된 전체 자동 테스트는 1,173개 실행, 47개 실패, 7개 오류였다.
그 이후 FacilityFinding·available·재시도 데이터 검사와 테스트 일부를 더 수정했다.
그 추가 수정 뒤 전체 테스트를 다시 실행하지 못했으므로 현재 실패 수는 새로 확인해야 한다.

모든 backend와 tests의 Python 파일은 ast.parse로 구문 확인했고 구문 오류는 없었다.
구문 통과는 실행 정상 동작이나 전체 테스트 통과를 뜻하지 않는다.

직전 안정 버전의 1,220개 통과 수치를 현재 상태의 결과로 보고하면 안 된다.

변경한 테스트와 이유는 다음과 같다.

- tests/test_plan_validation.py의 자연어 파싱 테스트를 구조화된 조건 병합·실제 값 대조 테스트로 일부 교체했다.
- tests/test_lodging_period.py는 서버 어휘 판정 테스트 대신 별도 분류 호출 없이 기존 에이전트를 실행하고 대화가 전달되는지 확인하도록 교체했다.
- tests/test_chat_lodging_period.py는 서버가 동의를 해석하던 테스트 대신 모델의 해석·질문 보존과 검증 피드백을 검사하도록 교체했다.
- 이 파일의 HOTEL, OPTIONS, history, location_messages는 tests/test_lodging_return_only.py가 사용하는 공용 fixture라 다시 보존했다.
- tests/test_chat_accessibility.py는 기존 숫자·자료 검증을 유지하고, 소개 보존·keep과 정보 답변 병행·시설 확인 값 검증 테스트로 일부 교체했다.
- tests/test_chat_reply_consistency.py는 서버가 답변 문장을 지우거나 바꿔야 한다는 테스트를 모델 답변 보존 테스트로 교체했다. 평가기 테스트는 남겼다.
- tests/test_chat_opening_hours.py의 최종 문구 덮어쓰기 기대를 운영시간에 따른 available 값 검증과 피드백 테스트로 일부 교체했다.

의도 정규식을 없애는 요청 때문에 기존 파싱·덮어쓰기 테스트 중 일부는 기대 동작이 달라진다.
그렇더라도 조건·조회 근거 검사 실패를 통과로 바꾸거나, 실제 결함이 있는 테스트를 무조건 삭제해서는 안 된다.

## 7. 이어서 할 작업

### 7.1 현재 실패를 새로 분류

프로젝트 루트에서 전체 테스트를 다시 실행하고 현재 실패 목록을 확보한다.

이 환경에서 사용한 Python은 /opt/homebrew/anaconda3/envs/jeju/bin/python이다.
기본 프로젝트 문서의 Python 3.12 환경으로도 실행할 수 있다.

```sh
/opt/homebrew/anaconda3/envs/jeju/bin/python -B -m unittest discover -s tests -p 'test_*.py' -q
```

마지막 실패는 숙소 관련 mock의 변경 필드 누락, 구식 파서 인자 사용, 자동 보정된 문구·일정을 기대하는 테스트, 식사 조건 누락 등에 집중됐다.

### 7.2 실제 구현의 미완성 부분을 먼저 해결

1. 검증 피드백의 통일을 완성한다.
   현재 create_chat의 StructuredOutputError와 최종 스키마 ValidationError는 바로 502로 종료한다.
   수정 가능한 응답 형식 오류도 같은 제한된 피드백 경로에 넣을 필요가 있다.
   외부 API·인증·DB·취소·시간 초과는 사용자 조건 위반과 구분한다.

2. 첫 시도의 미검증 숙소를 잠그는 문제를 해결한다.
   현재 locked_options는 _build_chat_result의 위치 근거 검사 전에 설정된다.
   잘못된 첫 숙소 좌표가 잠기면 다음 시도의 올바른 조회 결과도 거부할 수 있다.
   기존 prepare_lodging_retry_options는 실제 조회 결과로 재확인할 숙박일을 분리하므로 재사용할 수 있다.
   이 과정에서 다른 숙박일이나 교통·접근성 조건은 보존해야 한다.
   원문 키워드 기반 숙소 의도 검사를 다시 도입하면 안 된다.

3. 조건 잠금은 의미가 같은 구조화 값으로 비교한다.
   현재 schedule_changes와 visit_orders는 evidence까지 포함한 목록 전체를 정확히 비교한다.
   같은 조건의 순서를 바꾸거나 같은 사용자 문장의 다른 정확한 구절을 인용해도 거부될 수 있다.
   근거의 원문 존재 여부는 별도로 검사하고, 잠금 비교는 정규화된 실제 조건 값으로 처리한다.

4. 구조화된 조건 검증을 보강한다.
   시간의 경계·양수 체류, 서로 충돌하는 제한, 이미 없는 대상 삭제, 여러 날의 같은 방문 이동, 중복 방문 순서,
   기간 축소 후 삭제되는 일차의 조건 등을 살펴야 한다.
   최근의 read_schedule_constraints는 옮긴 직후라 조밀한 조건식과 중첩 컴프리헨션도 정리가 필요하다.

5. 초기 None 값 채우기의 범위를 점검한다.
   현재 read_fixed_trip_options는 기존 값이 None이면 모델 값으로 채우도록 해 둔 상태다.
   모델이 필요한 날짜·기간·수단을 문맥에서 확인했는지 구조화된 근거와 함께 다뤄야 한다.
   미확정 값을 임의로 채우는 길이 생기지 않도록 하되 자연어 의도를 서버가 다시 판정하는 방식은 피한다.

6. _build_chat_result의 새 정보 검증을 견고하게 한다.
   새 시설 검사에서 DB 호출의 예외·None·잘못된 place_id와 미확인 자료를 처리하는 부분을 점검한다.
   available의 휴무·입장 마감·폐장·자정 경계는 실제 운영정보와 대조해야 한다.
   데이터 오류와 모델의 잘못된 주장에 대한 피드백을 구분한다.
   기대값·실제값은 구조화하거나 읽기 쉬운 구체적 값으로 제공한다.

7. 새 필드의 프롬프트 설명을 마무리한다.
   findings, available, restaurant_retry_request와 옵션 변경 선언의 기준을 설명한다.
   기존 프롬프트에 남은 불필요한 서버 자동 처리 설명, 삭제된 필드·함수 참조를 확인한다.
   별도 의도 분류 모델·호출은 추가하지 않는다.

### 7.3 테스트를 계약 변경에 맞춰 갱신

우선 확인할 파일은 다음과 같다.

- tests/test_chat_route_endpoints.py의 식사 추가 mock에 schedule_changes의 add(@meal) 등 실제 조건을 명시해야 한다.
  점심 시각을 서버가 강제로 덮어써야 한다는 기대는 조건 위반→피드백→수정 일정 반환으로 바꾼다.
  식당 조회 실패의 모델 reply와 restaurant_retry_request도 실제 새 계약으로 검증한다.
- tests/test_chat_lodging_planning.py와 tests/test_chat_lodging_retry.py의 모델 mock은 실제 선언한 changed_options를 포함해야 한다.
  잘못된 최초 숙소를 올바른 조회 결과로 바꿀 수 있는지, 다른 숙박일을 보호하는지 검사한다.
- tests/test_lodging_name_aliases.py의 사용자 단어 매칭으로 숙소를 허용·거절하던 검사는 변경 대상이다.
  실제 조회 이름·주소·좌표와 선언된 변경 대상의 일치를 검증하도록 바꾼다.
- tests/test_lodging_return_only.py는 기간 동의와 기간 변경이 모델 출력에 선언되도록 fixture를 갱신한다.
- tests/test_chat_api.py의 확인 질문 mock은 LLM이 직접 질문과 needs_clarification을 반환하도록 바꾼다.
  사용자 입력을 서버가 다시 해석해 모델의 완성 일정을 무조건 질문으로 바꿔야 한다는 테스트는 새 방향에 맞지 않는다.
- tests/test_chat_auto_retry.py의 원문 시간 파싱 기대는 구조화된 schedule_changes 검증으로 바꾼다.
  시간·조건 오류를 실제 피드백하고 수정하는 검증은 유지한다.
- 에러 details의 field가 일부 options에서 response로 바뀌었다. 필드명을 유지할지 구체화할지 결정한 뒤 테스트를 맞춘다.

### 7.4 실제 실행으로 확인

자동 테스트가 정리되면 다음을 실제 Luna high로 실행한다.

- 인사와 서비스 소개.
- 장소 소개와 “일정은 만들지 마”의 결합.
- 초기 조건이 빈 상태의 귀환 포함 일정.
- 숙소 미정 일정과 숙소 선택·취소.
- 기간 연장 질문에 “응”과 “아니”로 답하는 연속 대화.
- 교통수단·여행일·방문 순서 변경.
- 실제 주변 식당 추가와 동일 식당의 식사 시각 변경.
- 식당 빈 결과·일시 오류·인증 오류의 모의 응답.
- 명시된 조건을 일부러 위반한 모델 결과를 주고 같은 Agent가 수정하는 흐름.

실제 모델과 도구를 쓰는 평가와 모의 자동 테스트를 구분한다.
현재 일정과 대화가 실제 앞선 응답에서 이어지는 대화도 확인한다.
성공 여부와 함께 모델 호출 횟수, 내부 재시도, 응답시간, 토큰을 기록한다.

### 7.5 문서와 최종 보고

docs/chat-planner.md는 새 내부 응답 필드·처리 흐름으로 아직 전면 갱신하지 못했다.
삭제한 lodging_period와 정규식 기반 강제 처리 설명이 남아 있을 수 있다.
새 결과는 별도 구간에 기록하고 과거 0011·0012의 수치와 혼합하지 않는다.
테스트 수가 달라지면 구식 파서 테스트를 교체한 범위를 설명한다.

## 8. 재현 자료와 백업

outputs/chat_eval/native-loop-20261119에 다음 자료를 보관했다.

- cases.json과 B01-1.json, B04-1.json, N01-1.json, C10-1.json, C11-1.json.
- 구현 전 서비스 파일 7개의 내용을 implementation-before.json에 보관했다.
  이는 이번 전환 직전의 파일 내용이며 이전 프로젝트 개선까지 포함한다.
- 실험 작업용 worker와 초안 변경 스크립트도 참고용으로 복사했다.
  초안 변경 스크립트를 현재 코드에 재실행하면 중복 수정·내용 손상이 날 수 있다.
- 마지막 전체 테스트 로그를 last-tests-incomplete.log로 복사했다. 이 로그 이후 수정도 있으므로 현재 결과는 재실행해야 한다.
- /tmp/jeju-intent-tests.log와 작업 스크립트는 임시 자료다.

implementation-before.json은 서비스 파일의 비교·부분 복구 자료다.
이번 전환에서 변경한 테스트 전체를 복원하는 백업은 아니다.
그 파일을 사용해 자동으로 모든 변경을 되돌리는 작업은 현재 요청에 포함돼 있지 않다.

비밀 키는 출력·문서·기록에 쓰지 않았다.
실제 평가에서는 이 대화에서 앞서 허용받은 /Users/day/projects/gachi-jeju/.env의 OpenAI·Kakao 설정을 프로세스 환경으로만 읽었다.
기본 개발 문서의 .env 환경을 사용해도 된다.

## 9. 다음 담당자에게 전달할 작업 요청

현재 미완성인 단일 Agent 검증 흐름 전환을 이어서 완성해 주세요.
이 문서와 현재 diff를 먼저 읽고, 기존 팀 작업을 보존해 주세요.
의도·문맥 판정 정규식과 최종 답변 덮어쓰기를 복구하지 마세요.
현재 Luna high와 기존 단일 에이전트가 요청을 해석하고 도구를 쓰도록 유지해 주세요.
서버는 실제 조건·필수 필드·근거를 검사하고, 실패를 같은 Agent에 돌려줘 제한 횟수 안에서 수정하게 해 주세요.
숙소 재시도·구조화 조건·정보 검증의 미완성 부분과 테스트를 완성하고 실제 요청으로 검증해 주세요.
