# 카카오모빌리티 자동차 길찾기 API 테스트

- 요청: `GET https://apis-navi.kakaomobility.com/v1/directions`
- 인증: 로컬 `.env`의 `KAKAO_REST_API_KEY`를 `Authorization: KakaoAK <키>` 헤더에 사용합니다.
- 입력: 제주 러브랜드 → 한림공원. `origin`과 `destination`은 경도, 위도 순서로 전달하고 `summary=true`를 지정했습니다.
- 재현 명령: `python scripts\check_kakao_driving.py`

| 응답 항목 | 확인값 |
| --- | --- |
| HTTP 상태 | `200` |
| `routes[0].result_code` | `0` (길찾기 성공) |
| `routes[0].summary.distance` | `30477`m |
| `routes[0].summary.duration` | `2713`초 (45분 13초) |

일정 계산에 장소 간 자동차 이동 거리와 예상 소요 시간을 사용할 수 있습니다. 소요 시간은 조회 시점의 예상값입니다.

[공식 API 문서](https://developers.kakaomobility.com/guide/navi-api/directions.html)