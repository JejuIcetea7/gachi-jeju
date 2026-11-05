**교통 도구 구현 및 검증**

검증일은 2026-11-05입니다.

**제공 기능**

| 도구 | 호출 함수 | 주요 출력 |
|---|---|---|
| 자동차 이동시간 | `backend.app.tools.driving.get_driving_time` | 거리(m), 예상 소요시간(초) |
| 버스 경로·실시간 정보 | `backend.app.tools.bus.get_bus_trip` | 후보 경로, 환승·도보 구간, 차량별 저상 여부·도착 정보·위치 |

두 함수는 다음 좌표를 키워드 인자로 받습니다.

```python
route_input = {
    "origin_latitude": 33.45211112,
    "origin_longitude": 126.488483,
    "destination_latitude": 33.39022486,
    "destination_longitude": 126.2396912,
}
```

좌표계는 WGS84입니다. API 키는 백엔드에서 불러옵니다.

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

**처리 흐름**

자동차 도구는 카카오모빌리티 응답을 거리와 소요시간으로 변환합니다.

버스 도구는 다음 순서로 처리합니다.

1. 카카오에서 대중교통 후보 경로를 조회합니다.
2. 버스 번호와 구간 전체의 정류장 순서를 제주 BIS 자료와 대조합니다.
3. 매칭된 승차 정류장의 도착 정보를 조회합니다.
4. `ROUTE_ID`와 `VH_ID`가 모두 같은 차량의 위치를 연결합니다.

같은 승차 정류장의 도착 정보는 한 번의 도구 호출 안에서 재사용합니다.
차량 위치 API는 필요한 경우 도구 호출 한 번당 최대 한 번 요청합니다.

**버스 응답에서 사용할 값**

| 필드 | 의미 |
|---|---|
| `routes[].duration_seconds`, `transfers` | 전체 예상 소요시간(초), 환승 횟수 |
| `routes[].segments[]` | 이동 순서대로 구성된 버스·도보 구간 |
| `segments[].bus_info.matches` | 제주 BIS 노선 ID, 승하차 정류장 ID와 순번 |
| `segments[].bus_info.vehicles` | 해당 구간에 연결된 차량별 실시간 정보 |
| `arrivals_requested_at`, `positions_requested_at` | 시스템이 각 실시간 API를 요청한 시각 |

`vehicles`의 주요 필드는 다음과 같습니다.

| 필드 | 의미 |
|---|---|
| `route_number`, `vehicle_id` | 노선 번호, 차량 식별자 |
| `low_floor` | 저상 여부. `true`, `false`, 미확인 시 `null` |
| `arrival_minutes`, `remaining_stops` | 현재 조회 기준 도착 예정 시간(분), 남은 정류장 수 |
| `last_stop_name` | 최근 통과 정류장 |
| `position` | `longitude`, `latitude`. 위치가 없으면 `null` |

경로의 소요시간과 실시간 도착 예정 시간은 각각 별도 값으로 제공합니다.
실시간 차량 정보는 조회 시점의 값이며, 이용자가 나중에 환승할 시점의 차량 정보를 확정하는 용도로 사용하지 않습니다.

**상태 해석**

자동차·카카오 조회 결과는 `ok`, `no_route`, `auth_error`,
`rate_limited`, `api_error`로 구분합니다.

버스 전체 `status`가 `partial`이면 경로를 유지하면서 문제가 있는 구간을 표시합니다.
`ok`인 경우에도 구간별 `no_arrivals`가 있을 수 있습니다.

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
이름의 띄어쓰기 차이를 정리하고 구간 전체의 정류장 이름·순번을 비교합니다.
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
| 오프라인 테스트 | 19개 모두 통과 |
| 자동차 실제 호출 | `ok`, 거리 34,860m, 예상 소요시간 2,836초 |
| 버스 실제 호출 | 전체 `ok`, 후보 경로 10개 모두 구간 매칭 |
| 첫 번째 버스 경로 | 예상 소요시간 5,390초, 환승 2회 |
| 실시간 연동 | 차량별 저상 여부, 도착 예정 시간, 남은 정류장, GPS 반환 확인 |

위 수치는 해당 실행에서 확인한 값입니다.
경로·소요시간·실시간 차량 정보는 조회 시점에 따라 달라집니다.

자동 테스트는 저장된 응답과 가상 실시간 데이터를 사용합니다.

```cmd
python -B -m unittest discover -s tests -p "test_*.py" -v
```

실제 API 호출은 다음 수동 확인 스크립트로 수행합니다.

```cmd
python -B -m scripts.check_transport_tools
```

API 오류, 도착 정보 없음, 정적 자료 누락, 순환 노선 오매칭,
다른 노선·정류장에 대한 별칭 적용 방지 등을 테스트했습니다.

**참고 문서**

- [카카오모빌리티 자동차 길찾기](https://developers.kakaomobility.com/guide/navi-api/directions.html)
- [카카오맵 대중교통 API](https://developers.kakao.com/docs/ko/kakaomap/rest-api#publictraffic)
- [제주 BIS Open API 안내](https://www.jeju.go.kr/help/open.htm?act=view&seq=967654)
- [제주 BIS 공식 노선 안내](https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule?viewtype=3)