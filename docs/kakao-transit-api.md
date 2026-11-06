# 카카오맵 대중교통 경로 API 테스트

- 요청은 `GET https://dapi.kakao.com/v2/routing/publictraffic`을 사용했습니다.
- 로컬 `.env`의 `KAKAO_REST_API_KEY`로 인증했습니다.
- 제주 러브랜드에서 한림공원까지의 좌표를 `start_x`, `start_y`, `end_x`, `end_y`에 전달했습니다.
- 재현 명령은 `python scripts\check_kakao_transit.py`입니다.

HTTP 200, 조회 상태 `OK`, 경로 10건을 확인했습니다. 첫 번째 경로는 예상 5389초(1시간 29분 49초), 환승 2회였고, 도보 구간 225m가 출력됐습니다.

| 버스 구간 | 응답에 포함된 노선 번호 |
| --- | --- |
| 제주도립미술관입구 → 노형교차로 | 240, 465, 466 |
| 노형교차로 → 한림매일시장입구 | 293 |
| 한림매일시장입구 → 한림공원 | 202, 784-1 |

응답에는 같은 노선 번호가 반복되며, 위 표는 구간별로 중복을 제거한 결과입니다. 저상버스 여부는 제주 버스정보시스템에서 별도로 조회할 수 있으며, 이번 테스트에서는 두 API의 결과를 연결하지 않았습니다.

[카카오맵 공식 API 문서](https://developers.kakao.com/docs/ko/kakaomap/rest-api)