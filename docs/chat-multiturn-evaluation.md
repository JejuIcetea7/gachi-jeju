# 연속 대화 /chat 평가

검증일: 2026-11-18

이전 응답의 대화 기록·선택 조건·일정을 다음 요청으로 이어 실제 `/chat` 응답을 평가합니다.
단일 요청 평가는 [최종 응답 평가](chat-api-evaluation.md)를 사용합니다.
기본 모드는 시나리오 검사이며, 실제 Agent·교통·숙소 API는 `--run`에서만 호출합니다.

────────────────────────────────────────

## 1. 기본 시나리오와 상태 전달

정의 파일은 [chat_multiturn_cases.json](../scripts/eval_cases/chat_multiturn_cases.json)입니다.

| 사례 | 첫 단계 | 다음 단계 |
|---|---|---|
| M01 | 날짜·기간·이동수단이 없는 요청에 확인 질문을 받습니다. | 답변한 조건으로 당일 일정을 생성합니다. |
| M02 | 한라수목원 60분 방문 일정을 생성합니다. | 같은 관광지의 체류시간을 90분으로 바꿉니다. |
| M03 | 숙소 미정인 1박 2일 관광 일정을 생성합니다. | 마지막 관광지 주변에서 검색한 숙소 후보를 선택하고 숙소 전후 이동을 반영합니다. |

성공한 응답의 사용자·도우미 메시지를 이어 붙이고 최근 20개만 다음 요청에 전달합니다.
응답의 `options`를 사용하며 `plan: null`이면 기존 일정을 유지합니다.
빈 일정은 새로운 일정이므로 이전 일정을 대체합니다.
HTTP 오류 뒤에는 실패한 대화를 추가하지 않고 전송했던 조건·기록·일정을 유지합니다.
자연어를 보고 구조화된 옵션을 미리 변경하지 않으며, 명시한 `options_patch`만 적용합니다.

이전 지도 경로·도구 조회 기록을 다음 시도의 검증 근거로 전달하지 않습니다.
`current_plan`에 이전 경로 ID가 있어도 새 응답의 조회 근거로 인정하지 않습니다.

────────────────────────────────────────

## 2. 실행과 재채점

프로젝트 루트의 평가 전용 프로세스에서 순차 실행합니다.
개발 서버를 별도로 켤 필요는 없습니다.

**정의 검사**

```cmd
.venv\Scripts\python.exe -B -m scripts.evaluate_chat_multiturn
```

API 호출·Git 조회·결과 파일 생성 없이 시나리오 3개의 구조만 검사합니다.

**실제 실행**

```cmd
.venv\Scripts\python.exe -B -m scripts.evaluate_chat_multiturn --run --case M02 --repeat 1 --label multiturn-duration-check --reference-date 2026-11-17
```

`--case`를 생략하면 모든 시나리오를 실행하며, `--repeat`의 기본값은 1입니다.
`--reference-date`는 상대 날짜 해석 기준이며 외부 API의 실제 조회 시각을 바꾸지 않습니다.
기본 사례의 여행 날짜는 2026-11-20~21이므로 다른 시기에 실행할 때는 함께 확인합니다.

**저장 결과 재채점**

```cmd
.venv\Scripts\python.exe -B -m scripts.evaluate_chat_multiturn --rescore "outputs\chat_eval\multiturn\20261117-100904-038236_multiturn-lodging-time-feedback-check" --label multiturn-lodging-rescore
```

저장된 시나리오·이전 응답·숙소 검색 응답으로 요청을 재구성합니다.
Agent·교통·숙소 API를 호출하지 않으며 원본을 수정하지 않고 새 폴더에 저장합니다.
현재 로컬 DB와 채점 코드를 사용하므로 변경 후 점수가 달라질 수 있습니다.
`--rescore`와 `--cases`, `--reference-date`, 반복 수 변경은 함께 사용하지 않습니다.

────────────────────────────────────────

## 3. 시나리오 정의와 검사

시나리오는 `case_id`·`description`·`turns`를 가지며, 단계마다 고유한 `turn_id`와 설명을 작성합니다.

| 필드 | 의미 |
|---|---|
| 첫 단계의 `request` | 첫 번째 `/chat` 요청 본문입니다. |
| 후속 단계의 `message`·`options_patch` | 이전 실제 응답을 이어 새 요청을 만듭니다. `false`·빈 배열·`null`도 명시적인 변경으로 유지합니다. |
| `expectation`·`expected_options`·`review_points` | 기대 응답 유형, 선택 조건의 기대값, 별도 내용 검토 항목입니다. |
| `expected_visits`·`preserve_visits` | 기대 방문의 전체 목록과 기존 관광지 유지를 검사합니다. 필요하면 방문 시간·체류시간도 지정합니다. |
| `lodging_selection` | `night`와 `candidate_index`로 실제 검색 후보를 선택하는 단계입니다. 일반 후속 메시지 대신 사용합니다. |

`expected_visits`를 지정하면 일차·장소 ID·횟수까지 전체 방문 목록을 대조합니다.
명시적인 빈 목록은 방문 0개를 뜻하며, 필드를 생략하면 해당 검사를 건너뜁니다.
`preserve_visits`는 기존 일차·장소 ID·횟수를 유지하는지 검사합니다.
후속 요청의 확정 옵션은 빈 목록도 포함해 검사하며, 의도적인 변경은 기대값으로 지정합니다.

숙소 검색 중심은 해당 숙박일의 마지막 방문지가 DB로 확인된 경우만 사용합니다.
앞선 관광지나 원래 출발지로 대신하지 않습니다.
화면과 같은 반경 5km·최대 5개를 조회하며, 후보 순번은 0부터 시작합니다.
선택한 후보의 ID·이름·주소·좌표를 숙소 옵션에 반영하고 나머지 숙박일 선택을 유지합니다.
실제 예약이나 숙소의 가격·잔여 객실·접근성 검증은 하지 않습니다.

────────────────────────────────────────

## 4. 결과와 실패 해석

결과 폴더는 `outputs/chat_eval/multiturn/<날짜-시각>_<이름표>/`입니다.

| 파일 | 내용 |
|---|---|
| `metadata.json` | 전체 실행 계획, 원래 생성 설정, 실행·재채점 모드와 채점 코드 상태입니다. |
| `runs.jsonl` | 단계별 요청·HTTP 응답·시도별 근거·숙소 검색 응답입니다. 채점 전에 저장합니다. |
| `scores.jsonl` | 기존 API 검사와 `turn_link`·`turn_visits`·`turn_lodging`·`scenario_contract` 판정입니다. |
| `summary.csv`·`report.md` | 단계별 결과, 시나리오 전체 판정, 호출 수·처리시간과 검토 항목입니다. |

실제 실행은 단계 준비·채점 오류나 자동 실패가 발생하면 후속 단계를 실행하지 않습니다.
계획에 있지만 실행 기록이 없는 단계는 `not_recorded`로 실패 처리하며 호출 수를 0으로 추정하지 않습니다.
시나리오 전체 통과에는 모든 단계의 자동 통과가 필요합니다.
재채점은 새 기준에서 앞 단계가 실패해도 이미 저장된 뒤 단계의 응답을 계속 검사합니다.

자동 통과는 내용 검토 완료를 뜻하지 않습니다.
출력의 `requires_manual_review`와 검토 항목은 별도로 유지합니다.
재채점의 호출 수·처리시간은 원래 실행 당시 기록이며 새 API 호출량이 아닙니다.
`find_location` 도구 호출 수는 외부 위치 검색 횟수와 다를 수 있습니다.
`source: selected_lodging`·`lookup_performed: false`는 현재 선택 좌표를 재사용한 응답입니다.
불필요한 숙소 위치 검색이 없었는지는 자동 통과만으로 판정하지 않으며 시도별 도구 기록을 별도로 검토합니다.
이전 단일 요청 평가 로그와 기록 형식이 다르므로 서로의 CLI로 재채점하지 않습니다.

────────────────────────────────────────

## 5. 확인한 범위

연속 대화 평가 도입과 당시 오류 보완 후 Python 전체 899개가 통과했습니다.
연속 대화 검사 68개, 귀환 표현 검사 9개, 시간 계산 피드백 검사 4개를 기존 818개에 추가했습니다.
M01·M02와 마지막 M03의 두 단계 응답을 검토했고, M02·M03의 CLI 재채점과 원본 보존을 확인했습니다.

귀환 설명 누락과 실제 재시도 후 시간 계산 실패도 관찰했습니다.
당시 마지막 M03에서는 두 단계 모두 첫 Agent 시도에 응답해 새 계산 피드백이 실제로 사용되지 않았습니다.
반복 성공률, 피드백의 효과와 브라우저 화면은 해당 평가에서 확인하지 않았습니다.
실행 폴더와 검토 내용은 [연속 대화 검증 기록](experiments/0005-chat-multiturn-evaluation.md)을 참고합니다.

이후 숙소 선택 보완에서는 M03의 숙소 외부 위치 검색 0회와 실제 경로 ID 재생성 성공을 확인했습니다.
관련 회귀 검사 147개가 통과했고 대화로 숙소를 변경하는 화면은 사용자 확인을 받았습니다.
후속 진단과 확인 범위는 [숙소 선택·변경 검증](experiments/0006-lodging-selection-consistency.md)에 구분해 기록했습니다.

────────────────────────────────────────

## 6. 여러 날의 동선·숙소 검증

별도 사례 파일 [chat_route_multiday_cases.json](../scripts/eval_cases/chat_route_multiday_cases.json)에 RM01을 정의했습니다.
기본 M01~M03 파일은 유지하며 `--cases`로 선택합니다.

```cmd
.venv\Scripts\python.exe -B -m scripts.evaluate_chat_multiturn --cases scripts/eval_cases/chat_route_multiday_cases.json
.venv\Scripts\python.exe -B -m scripts.evaluate_chat_multiturn --run --cases scripts/eval_cases/chat_route_multiday_cases.json --case RM01 --repeat 1 --label route-multiday-check --reference-date 2026-11-17
```

RM01은 자동차 1박 2일의 권역 배치와 첫 숙박일의 실제 후보 선택을 두 단계로 확인합니다.
첫 단계의 날짜별 장소는 고정하지 않아 권역 묶기·네 장소 각 60분·누락 여부는 별도 내용 검토 대상입니다.
두 번째 단계에서는 방문일·장소·횟수·체류시간과 선택 조건을 유지하는지 검사합니다.
날짜별 순서 비교의 경계와 최종 지도 경로 연결도 저장된 조회 기록으로 검토합니다.

```cmd
.venv\Scripts\python.exe -B -m scripts.evaluate_chat_multiturn --rescore "outputs\chat_eval\multiturn\20261117-233152-938842_route-multiday-details-check" --label route-multiday-rescore
```

두 단계의 자동 통과와 원본 보존을 확인했습니다.
이후 제주신라호텔로 변경한 진단과 사용자 화면 확인은 별도 기록이며 RM01의 세 번째 자동 평가 단계로 집계하지 않습니다.
상세 조회 선행과 순서 비교 경계 검사는 실제 서버 처리에 포함되지만, 재채점이 서버의 전체 검증 파이프라인을 다시 실행하지는 않습니다.
실패 기록·후속 보완·검토 범위는 [실험 0008](experiments/0008-route-efficiency-multiday.md)을 참고합니다.