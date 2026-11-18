# 여행 일정 PDF 다운로드

화면에 표시된 전체 여행 일정과 그 일정에 적용된 옵션을 PDF로 저장합니다.
선택한 일차만 저장하거나 아직 재생성하지 않은 옵션을 이전 일정에 섞지 않습니다.
PDF 이메일 발송과 서버 설정은 [여행 일정 PDF 이메일 발송](itinerary-email.md)을 참고합니다.

────────────────────────────────────────

## 1. 최초 준비

프로젝트 루트에서 실행합니다.

```cmd
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -B -m scripts.prepare_itinerary_pdf_fonts
```

준비 스크립트는 Google Fonts의 나눔고딕 Regular·Bold와 OFL.txt를
`backend/app/assets/fonts/`에 내려받습니다.
두 글꼴의 SHA-256을 확인하고 기존 파일을 임의로 덮어쓰지 않습니다.
글꼴과 라이선스 세 파일은 배포 파일과 Git 관리 대상에 함께 포함합니다.
PDF의 한글 표시는 저장소에 포함된 로컬 글꼴을 사용합니다.

한국어 글꼴은 PDF에 포함됩니다.
ReportLab은 4.4.9로 고정하며 pypdf는 생성된 내용과 글꼴을 검사하는 테스트에서 사용합니다.

────────────────────────────────────────

## 2. 화면 동작과 PDF 내용

일정 생성 후 '전체 일정 PDF 다운로드'를 누릅니다.
화면에서 선택한 날짜와 관계없이 전체 날짜를 저장합니다.
일정 생성 요청 중과 PDF 준비 중에는 버튼을 비활성화합니다.
새 여행을 시작하거나 일정이 교체되면 이전 다운로드 요청의 결과를 반영하지 않습니다.

PDF에는 여행 날짜·출발지·이동 조건·숙박일별 선택 숙소,
일차별 방문·이동 시간표, 접근성 메모, 경고와 계획 전제를 담습니다.
버스의 승하차 안내는 최종 일정에 유일하게 연결된 경로만 사용합니다.
미확인 정보는 미확인 상태로 유지하며 지도 화면과 사진을 캡처하지 않습니다.
관광지 방문이 없는 귀환 전용 일차와 빈 일차도 유지합니다.

브라우저에 파일을 전달한 뒤 다운로드 목록 확인을 안내합니다.
실제 파일 저장 위치는 브라우저 설정에 따릅니다.

등록된 방문 장소의 대표 사진을 웹 일정표와 같은 기준으로 넣습니다. 사진을 읽지 못한 장소는 방문 정보만 표시합니다.

────────────────────────────────────────

## 3. API

| 구분 | 값 |
|---|---|
| 백엔드 | POST /itineraries/pdf |
| 개발 프록시 | POST /api/itineraries/pdf |
| 요청 | options, plan, warnings, transit_routes |
| 정상 응답 | application/pdf, 첨부 파일 Content-Disposition |
| 입력 오류 | 400 invalid_request |
| 글꼴·라이브러리 준비 오류 | 503 pdf_unavailable |

프론트엔드는 현재 표시된 `planResult`의 스냅샷만 보냅니다.
대화 기록, 모델 메타데이터와 지도 도로선은 보내지 않습니다.
서버는 요청 형식·일차 범위·문장과 항목 수를 검사합니다.
저장 기능은 전달받은 일정의 추가 생성이나 사실 재검증을 수행하지 않습니다.

한 번에 366일차까지, 일정 항목 2,000개 이하를 지원합니다.
문장은 각각 4,000자 이하, 전체 문자열은 300,000자 이하입니다.
프론트엔드의 PDF 준비 제한 시간은 30초입니다.
정상 응답의 Content-Type과 PDF 파일 식별자를 검사하고 오류 응답을 파일로 저장하지 않습니다.
응답에는 `Cache-Control: private, no-store`를 지정하며 서버 디스크에 PDF를 저장하지 않습니다.

────────────────────────────────────────

## 4. 검증

```cmd
.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_itinerary_pdf.py" -v
node --test tests/frontend/test_itinerary_pdf.mjs
npm --prefix frontend run lint
npm --prefix frontend run build
```

자동 검사는 한국어 글꼴 포함, 전체 날짜·숙소·메모 보존,
긴 문장과 페이지 나눔, 버스 미확인 안내, 잘못된 입력,
동시 PDF 생성, 정상·실패 HTTP 응답, 다운로드 오류·취소를 다룹니다.
실제 브라우저의 파일 저장과 PDF 표시 여부는 별도로 확인합니다.

참고: [ReportLab 글꼴 문서](https://docs.reportlab.com/reportlab/userguide/ch3_fonts/),
[나눔고딕 원본과 라이선스](https://github.com/google/fonts/tree/main/ofl/nanumgothic).
