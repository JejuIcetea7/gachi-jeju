# 제주 버스정보시스템 API 테스트

- 호출 환경: Python 3.12, urllib, truststore
- 요청 방식: HTTPS GET, `station_id` 파라미터 사용. 테스트는 API 키 없이 실행했습니다.
- API 가이드: https://www.jeju.go.kr/help/open.htm?act=view&seq=967654

| 조회 | 요청 | 확인 결과 |
| --- | --- | --- |
| 정류장 경유 노선 | `/api/searchRouteByStationList.do?station_id=405001897` | HTTP 200. 제주국제공항1의 정류장명, 노선 ID, 노선 번호, 종점명을 확인했습니다. |
| 실시간 도착 정보 | `/api/searchArrivalInfoList.do?station_id=405000153` | HTTP 200. 제주버스터미널[남]에서 도착 정보 3건을 확인했습니다. |

실시간 응답에서 `ROUTE_NUM`(노선 번호), `PREDICT_TRAV_TM`(예상 도착까지 남은 분), `LOW_PLATE_TP`(저상버스 여부)를 확인했습니다. 461번 19분, 462번 23분, 43-1번 42분이었으며 모두 `LOW_PLATE_TP=Y`였습니다.

앞선 두 정류장에서는 HTTP 200이어도 스크립트가 도착 정보를 0건으로 집계했습니다. 서비스에서는 이 경우 저상버스 여부를 ‘확인할 수 없음’으로 표시합니다.