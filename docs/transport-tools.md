**교통 도구 구현 및 검증**

자동차·버스 실제 API 검증일은 2026-11-05입니다.
도보 추가와 전체 테스트 검증일은 2026-11-06입니다.

**제공 기능**

| 도구 | 호출 함수 | 주요 출력 |
|---|---|---|
| 자동차 이동시간 | `backend.app.tools.driving.get_driving_time` | 거리(m), 예상 소요시간(초) |
| 버스 경로·실시간 정보 | `backend.app.tools.bus.get_bus_trip` | 후보 경로, 환승·도보 구간, 차량별 저상 여부·도착 정보·위치 |
| 도보 경로 조회 | `backend.app.tools.walking.get_walking_route` | 도보 거리(m), 예상 소요시간(초), 이동 안내·경로 좌표 |

세 도구가 공통으로 받는 좌표는 다음과 같습니다.
아래는 자동차·버스 검증에 사용한 좌표 예시입니다.

```python
route_input = {
    "origin_latitude": 33.45211112,
    "origin_longitude": 126.488483,
    "destination_latitude": 33.39022486,
    "destination_longitude": 126.2396912,
}
```

좌표계는 WGS84입니다.
API 키는 백엔드 설정 함수에서 불러옵니다.
도보 도구에는 탐색 옵션인 `route_mode`를 추가로 지정할 수 있습니다.

**실행 환경**

Python 3.12와 프로젝트용 가상환경을 사용합니다.
프로젝트 루트에서 의존성을 설치합니다.

```cmd
python -m pip install -r backend\requirements.txt
```

프로젝트 루트의 `.env`에 다음 환경변수를 설정합니다.

```dotenv
KAKAO_REST_API_KEY=발급받은_REST_API_키
```

`.env`는 Git에서 제외하고, `.env.example`에는 변수 이름과 빈 값만 공유합니다.
인증서 처리는 `truststore`를 사용합니다.

**처리 흐름**

자동차 도구는 카카오모빌리티 응답을 이동 거리와 예상 소요시간으로 변환합니다.

버스 도구는 다음 순서로 처리합니다.

1. 카카오에서 대중교통 후보 경로를 조회합니다.
2. 버스 번호와 구간 전체의 정류장 순서를 제주 BIS 자료와 대조합니다.
3. 매칭된 승차 정류장에서 해당 노선의 실시간 도착 정보를 조회합니다.
4. `ROUTE_ID`와 `VH_ID`가 모두 일치하는 차량의 GPS 좌표를 연결합니다.

같은 승차 정류장의 도착 정보는 한 번의 도구 호출 안에서 재사용합니다.
차량 위치 API는 필요한 경우 도구 호출 한 번당 최대 한 번 요청합니다.

도보 도구는 카카오맵 도보 API를 호출해 전체 거리·시간과 단계별 안내·경로 좌표를 반환합니다.
버스 경로 안의 도보 구간과 함께, 출발지부터 목적지까지 전부 도보로 이동하는 경우를 지원합니다.

**버스 응답에서 사용할 값**

| 필드 | 의미 |
|---|---|
| `routes[].duration_seconds`, `routes[].transfers` | 전체 예상 소요시간(초), 환승 횟수 |
| `routes[].segments[]` | 이동 순서대로 구성된 버스·도보 구간 |
| `routes[].segments[].bus_info.matches` | 제주 BIS 노선 ID, 승하차 정류장 ID와 순번 |
| `routes[].segments[].bus_info.vehicles` | 해당 구간에 연결된 차량별 실시간 정보 |
| `bus_info.arrivals_requested_at`, `bus_info.positions_requested_at` | 시스템이 각 실시간 API를 요청한 시각(UTC) |

`vehicles`의 주요 필드는 다음과 같습니다.

| 필드 | 의미 |
|---|---|
| `route_id`, `route_number`, `vehicle_id` | 노선 ID, 노선 번호, 차량 식별자 |
| `low_floor` | 차량별 저상 여부. `True`, `False`, 미확인 시 `None` |
| `arrival_minutes`, `remaining_stops` | 현재 조회 기준 도착 예정 시간(분), 남은 정류장 수 |
| `last_stop_name` | 최근 통과 정류장 |
| `position` | `longitude`, `latitude`. 위치가 없으면 `None` |

JSON으로 변환하면 `True`, `False`, `None`은 각각 `true`, `false`, `null`입니다.

경로의 소요시간과 실시간 도착 예정 시간은 각각 별도 값으로 제공합니다.
실시간 차량 정보는 조회 시점의 값입니다.
이용자가 나중에 환승할 시점의 차량은 해당 시점의 정보를 다시 확인해야 합니다.

**도보 도구 사용 방법**

호출 함수는 `backend.app.tools.walking.get_walking_route`입니다.
아래는 실제 검증에 사용한 한림공원 인근 좌표입니다.

```python
from backend.app.tools.walking import get_walking_route

result = get_walking_route(
    origin_latitude=33.39022486,
    origin_longitude=126.2396912,
    destination_latitude=33.3945,
    destination_longitude=126.239,
    route_mode="BROAD_FIRST",
)
```

| 반환 필드 | 의미 |
|---|---|
| `status` | 처리 상태 |
| `distance_m`, `duration_seconds` | 도보 거리(m), 지도 API 기준 예상 이동시간(초) |
| `landing_url` | 카카오맵 도보 길찾기 링크 |
| `steps[]` | 단계별 거리·시간·안내 문구 |
| `steps[].path_points` | `[경도, 위도]` 형식의 경로 좌표 |

각 `steps` 항목에는 `distance_m`, `duration_seconds`, `guidance`, `path_points`가 들어갑니다.

탐색 옵션은 다음과 같습니다.

| `route_mode` | 의미 |
|---|---|
| `BROAD_FIRST` | 넓은 길 우선. 현재 기본값 |
| `SHORTEST` | 최단 경로 |
| `ACCESSIBLE` | 편안한 길 |

이번 실제 API 검증은 `BROAD_FIRST`로 수행했습니다.
`ACCESSIBLE` 옵션의 파라미터 전달은 오프라인 테스트로 확인했습니다.

도보 경로의 접근성을 판단할 때는 계단·경사 등 무장애 자료를 함께 활용해야 합니다.
이용자의 보행 속도와 휴식 조건은 후속 일정 구성 단계에서 반영할 항목입니다.

**상태 해석**

자동차·대중교통·도보 조회의 기본 상태는 다음과 같습니다.

| 상태 | 의미 |
|---|---|
| `ok` | 조회 및 응답 처리가 정상적으로 수행됨 |
| `no_route` | 요청 조건에서 경로를 반환받지 못함 |
| `auth_error` | API 키 또는 호출 권한 문제 |
| `rate_limited` | 호출 횟수·금액 한도 초과 |
| `api_error` | 그 외 API·네트워크·응답 처리 오류 |

도보의 `no_route` 응답에는 `SAME_POINT`, `TOO_FAR_AWAY` 등 제공기관의 상세 상태가 `reason`으로 포함됩니다.

버스 전체 `status`가 `partial`이면 경로를 유지하면서 문제가 있는 구간을 표시합니다.
전체 상태가 `ok`인 결과에도 구간별 `no_arrivals`가 포함될 수 있습니다.

| `bus_info.status` | 의미 |
|---|---|
| `matched` | 정류장 순서와 승하차 ID 쌍을 확인함 |
| `static_data_missing` | 필요한 노선 번호 또는 정류장 파일이 부족함 |
| `mapping_failed` | 저장된 자료에서 일치하는 정류장 순서를 찾지 못함 |
| `ambiguous_match` | 여러 정류장 쌍 또는 같은 노선의 여러 탑승 구간이 남음 |
| `static_data_error` | 정적 데이터 읽기 또는 처리 오류 |

| `bus_info.realtime_status` | 의미 |
|---|---|
| `ok` | 해당 차량의 도착 정보와 위치를 연결함 |
| `no_arrivals` | 매칭된 노선의 도착 정보가 조회 시점에 비어 있음 |
| `arrival_error` | 도착 정보 API 조회 실패 |
| `position_error` / `position_missing` | 위치 API 조회 실패 / 차량별 유효 좌표 누락 |
| `not_requested` | 정적 매칭 상태 등에 따라 실시간 조회를 수행하지 않음 |

위치 조회에 문제가 생겨도 확보한 도착 시간·저상 여부·최근 통과 정류장은 유지합니다.

**정적 데이터와 매칭 규칙**

| 저장 대상 | 위치 |
|---|---|
| 전체 노선 목록 994건 | `data/processed/bus/routes.json` |
| 노선별 정류장 파일 65개 | `data/processed/bus/route_stops/` |
| 확인한 표기 대응 | `bus_matching.py`의 `STOP_NAME_ALIASES` |

현재 정류장 자료를 확보한 노선 번호는
`102`, `202`, `240`, `270`, `293`, `465`, `466`, `784-1`입니다.

다른 번호가 포함된 경로는 자료 확보 상태에 따라 `static_data_missing`을 반환합니다.

매칭 결과는 저장된 정류장 자료를 바탕으로 호출 시 계산합니다.
이름의 띄어쓰기 차이를 정리하고 구간 전체의 정류장 이름·연속된 순번을 비교합니다.
이 방식으로 순환 노선에서 크게 돌아가는 동명 정류장 후보를 제외합니다.

102번의 BIS 정류장 ID `405002811`은
`제주버스터미널(가상정류소)`를 카카오의 `제주버스터미널`과 대응시킵니다.
나머지 경유 순서와 공식 기점 안내를 근거로 등록했으며,
102번·해당 ID·해당 원래 이름에 한정해 적용합니다.
실제 승차 위치와 접근성은 별도로 확인할 항목입니다.

실시간 도착 정보와 차량 위치는 실행 중 메모리에서 사용합니다.

정류장 자료를 추가하는 명령입니다. 기존 파일은 재사용합니다.

```cmd
python -B -m scripts.export_jeju_bus_static --route-numbers 102 202 240 270 293 465 466 784-1
```

특정 정류장 파일을 갱신하려면 노선 ID를 직접 지정합니다.

```cmd
python -B -m scripts.export_jeju_bus_static 405410221
```

**검증 결과**

| 항목 | 확인 결과 |
|---|---|
| 전체 오프라인 테스트 | 27개 모두 통과 |
| 자동차 실제 호출 | `ok`, 거리 34,860m, 예상 소요시간 2,836초 |
| 버스 실제 호출 | 전체 `ok`, 후보 경로 10개 모두 구간 매칭. 첫 경로는 5,390초, 환승 2회 |
| 버스 실시간 연동 | 차량별 저상 여부, 도착 예정 시간, 남은 정류장, 최근 통과 정류장, GPS 반환 확인 |
| 도보 실제 호출 | `ok`, 거리 718m, 예상 이동시간 614초, 안내 6단계, 좌표 21개 |

위 수치는 해당 실행에서 확인한 값입니다.
경로·소요시간·실시간 차량 정보는 조회 시점에 따라 달라집니다.

자동 테스트는 저장된 응답과 가상 응답을 사용합니다.

| 테스트 영역 | 테스트 수 |
|---|---:|
| 자동차 조회 | 4개 |
| 카카오 대중교통 조회 | 3개 |
| 버스 정류장 매칭 | 6개 |
| 버스 도구 통합 | 6개 |
| 도보 조회 | 8개 |

전체 테스트 실행 명령입니다.

```cmd
python -B -m unittest discover -s tests -p "test_*.py" -v
```

도보 테스트 8개만 실행하는 명령입니다.

```cmd
python -B -m unittest discover -s tests -p "test_kakao_walking.py" -v
```

도보 테스트는 정상 응답, 좌표·탐색 옵션 전달, 경로 없음,
인증·호출 한도 오류, 잘못된 응답·JSON, 네트워크 오류를 확인합니다.

버스 테스트는 도착 정보 없음, 정적 자료 누락, 실시간 API 실패,
순환 노선 오매칭, 별칭 적용 범위와 중복 요청 방지 등을 확인합니다.

**수동 API 확인**

자동차·버스의 실제 API 호출은 다음 스크립트로 확인합니다.

```cmd
python -B -m scripts.check_transport_tools
```

도보 API 원본 응답 확인과 테스트 샘플 저장에는 다음 스크립트를 사용합니다.

```cmd
python -B -m scripts.check_kakao_walking
```

이 스크립트는 `tests/sample_responses/kakao_walking_success.json`을 갱신합니다.
도보 테스트의 예상 수치는 저장한 검증 응답을 기준으로 합니다.
샘플을 갱신하면 해당 응답의 거리·시간·단계 수·좌표 수와 테스트 기대값을 함께 확인합니다.

**참고 문서**

- [카카오모빌리티 자동차 길찾기](https://developers.kakaomobility.com/guide/navi-api/directions.html)
- [카카오맵 대중교통 API](https://developers.kakao.com/docs/ko/kakaomap/rest-api#publictraffic)
- [카카오맵 도보 API](https://developers.kakao.com/docs/ko/kakaomap/rest-api#walk)
- [제주 BIS Open API 안내](https://www.jeju.go.kr/help/open.htm?act=view&seq=967654)
- [제주 BIS 공식 노선 안내](https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule?viewtype=3)