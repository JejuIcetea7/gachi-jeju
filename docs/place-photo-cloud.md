# 통합 사진의 AWS 업로드와 화면 연결

사진은 비공개 S3 버킷에 `photos_web/P001/P001_0001.webp` 구조로 저장하고,
CloudFront OAC를 통해 읽습니다. CloudFront 사진 URL은 사이트 방문자에게 공개됩니다.
S3 자체의 퍼블릭 액세스 차단은 유지합니다.

## 콘솔 업로드 후 화면 연결

현재 연결은 S3 콘솔로 올린 사진과 CloudFront 공개 URL을 사용합니다.
프론트의 사진 표시와 아래 주소 생성에는 IAM 사용자, AWS CLI 로그인과 AWS 키가 필요하지 않습니다.
업로드 전용 IAM 사용자와 직접 만든 IAM 정책을 정리해도
CloudFront OAC와 S3 버킷 정책을 유지하면 사진 전달 경로는 유지됩니다.

버킷 최상위에서 `photos_web` 폴더 전체를 올린 뒤,
프로젝트 루트에서 실제 CloudFront 도메인으로 주소 목록을 생성합니다.

```cmd
.venv\Scripts\python.exe -B scripts\export_photo_cloud_urls.py --cdn-base-url https://dexample.cloudfront.net
```

`data/processed/place_photos_cloud.csv`에 사진별 주소를 기록합니다.
원본 웹용 CSV의 내용 검사값·크기·관련 장소 목록을 유지하고 `upload_status`는
`url_generated`, `verified_at`는 빈 값으로 기록합니다.
주소를 만들었다는 뜻이며 원격 사진 전체의 존재나 내용 검사값을 확인했다는 뜻은 아닙니다.
이 목록은 공개 URL과 장소 매핑을 공유하기 위한 메타데이터이므로 Git 관리 대상입니다.
사진 파일은 기존과 같이 Git에서 제외합니다.

백엔드는 이 CSV가 있으면 웹용 CSV와 사진 ID·장소 목록·경로·크기·내용 검사값을 대조합니다.
사진이 등록되지 않은 장소는 빈 목록으로 반환합니다.
목록이 손상되거나 일부 사진이 누락되면 정상적인 빈 목록 대신 공통 500 오류를 반환합니다.
주소 목록이 없으면 기존 원본 사진 제공 방식을 사용합니다.

사진은 일정표의 장소명을 누른 상세 창에서 **사진** 보기로 표시합니다.
목록 API는 `photo_id`, HTTPS CloudFront `url`, 장소별 `contexts`를 추가로 반환합니다.
`contexts`에는 `spot_name`, `description`, `mapping_confirmed`만 포함합니다.
서로 다른 장소의 설명을 섞지 않으며 내부 원본 경로는 공개하지 않습니다.
미확인 연결은 사진 설명에 **장소 연결 미확인**, 빈 설명은 **설명 없음**으로 표시합니다.
프론트는 CloudFront 사진 주소를 직접 사용하며 오래된 클라이언트의 파일 API 요청은
동일 사진으로 307 리다이렉트합니다.

## 상세 정보와 사진 설명의 중복 표시

시설명과 전체 설명이 일치하는 항목은 사진 쪽에 남기고 시설·동선 보기에서 숨깁니다.
비교할 때만 공백·줄바꿈과 유니코드 조합 차이를 정리하고 표시 원문은 변경하지 않습니다.
같은 문장이라도 시설명이 다르거나, 일부 문장·숫자·계단 유무가 다르면 숨기지 않습니다.
시설 조사 값은 조사 분류와 항목 이름까지 사진 설명에 포함되어야 같은 정보로 취급합니다.

사진 목록 조회가 성공했고 해당 장소 연결이 확인된 사진만 기준으로 사용합니다.
사진 조회 중·오류·빈 목록, 장소 연결 미확인 또는 실패한 이미지의 설명으로는
기존 상세 정보를 숨기지 않습니다. 다른 정상 사진에 같은 설명이 있으면 그 사진으로 제공합니다.
숨긴 항목이 있으면 시설·동선 보기에 ‘사진 보기’ 링크를 제공합니다.
모든 항목을 사진 쪽에 표시하는 섹션에는 ‘자료 미등록’ 안내를 표시하지 않습니다.
이번 일정의 접근성 안내와 기본 정보는 계속 표시합니다.

중복 제거는 화면 표시만 조정하며 API와 원본 CSV·DB를 변경하지 않습니다.
실제 한림공원의 지점 설명 35건 중 시설명·설명이 일치하는 23건이 사진으로 제공되며,
나머지 12건은 시설·동선 보기에 남습니다.
별도 후보 코드에서 관련 프론트 테스트 34개, 정적 렌더링 검사 8개, ESLint 검사와 Vite 메모리 빌드를 통과했습니다.
사용자가 한림공원 사진 창에서 중복 설명이 정리된 화면을 확인했습니다.
사진 조회 실패·이미지 실패·미확인 연결의 복원 분기는 자동 검사로 확인했습니다.

2026-11-15 확인 범위:

| 항목 | 결과 |
| --- | --- |
| 로컬 원본 ZIP·추출 사진 | 사용자 실행을 포함해 3,834개, 1,849,804,936바이트의 크기·SHA-256이 웹용 CSV와 일치했습니다. |
| 콘솔 업로드·CloudFront 표본 | 사용자가 업로드 성공과 P001_0001 사진 주소 표시를 확인했습니다. 원격 파일 전체의 SHA-256을 검증한 것은 아닙니다. |
| 실제 목록 대조 | 후보 코드에서 사진 3,834개와 DB에 있는 119개 장소의 연결을 확인했습니다. 사진 매핑이 없는 장소는 51개입니다. |
| Python 전체 자동 검사 | 사용자 실행으로 558개가 통과했습니다(실패 0개). |
| 프론트 전체 자동 검사 | 프로젝트 코드에서 108개가 통과했습니다(실패 0개). |
| 프론트 lint·프로덕션 빌드 | 사용자 실행으로 모두 통과했습니다. |
| `git diff --check` | 최종 코드 검사에서 출력이 없었습니다. |
| 애플리케이션 화면 | 사용자가 한림공원 일정의 이미지·설명 표시와 중복 설명 정리를 확인했습니다. 전체 사진의 전수 검수는 아닙니다. |

아래 S3 자동 업로드 스크립트는 반복 업로드를 위한 선택 기능입니다.
콘솔에서 올린 사진을 화면에 연결할 때는 위 주소 생성 스크립트를 사용합니다.

## 로컬 사진과 목록 검증

ZIP 안의 `photos_web` 폴더를 `data/processed` 바로 아래에 풉니다.
`data/processed/photos_web/photos_web`처럼 중복된 폴더를 만들지 않습니다.
사진 파일과 ZIP은 Git에 추가하지 않습니다. 기존 `.gitignore`에서 제외합니다.

```cmd
.venv\Scripts\python.exe -B scripts\upload_place_photos.py
```

이 명령은 사진 목록, 관련 장소 ID, 경로, 파일 크기, SHA-256을 확인합니다.
기본 모드에서는 AWS SDK를 가져오거나 AWS에 접속하지 않습니다.
사진과 원본 CSV를 변경하지 않으며 결과만
`outputs/photo_web/cloud_upload/validation.json`에 기록합니다.

추출하기 전에 ZIP 내용만 검사할 수도 있습니다. 파일을 추출하지 않습니다.

```cmd
.venv\Scripts\python.exe -B scripts\upload_place_photos.py --archive "ZIP의 전체 경로"
```

## 업로드 실행 조건

업로드는 `--upload`를 명시한 경우에만 실행합니다. 전체 로컬 검증이 통과한 뒤 AWS에 접속합니다.
별도 업로드용 IAM 사용자에게 해당 사진 버킷의 `s3:ListBucket` 권한과
`photos_web/*`의 `s3:GetObject`, `s3:PutObject` 권한을 부여합니다.
`HeadBucket`과 존재하지 않는 객체의 `HeadObject` 응답 구분에 버킷 목록 권한이 필요합니다.
객체 삭제, ACL 변경, 버킷 정책 수정 권한은 업로드에 필요하지 않습니다.

AWS CLI v2.32.0 이상과 Boto3 v1.41.0 이상 및 CRT가 필요합니다.
프로젝트 의존성에는 Boto3의 CRT 추가 기능을 포함한 버전을 고정합니다.
IAM 사용자에게 `SignInLocalDevelopmentAccess`를 추가하고,
아래 명령이 여는 브라우저에서 업로드용 사용자로 로그인합니다.
리전 입력은 `ap-northeast-2`입니다. 장기 액세스 키를 코드나 `.env`에 넣지 않습니다.

```cmd
.venv\Scripts\python.exe -m pip install -r requirements.txt
aws login --profile gachi-photos
```

다음 값은 형식 예시입니다. 실제 버킷, CloudFront 도메인과 버킷 소유 계정 ID로 바꿉니다.

```cmd
.venv\Scripts\python.exe -B scripts\upload_place_photos.py --upload --profile gachi-photos --bucket BUCKET-NAME --cdn-base-url https://dEXAMPLE.cloudfront.net --expected-owner 123456789012
```

최대 4개의 작업을 동시에 실행합니다. 각 요청에 기대하는 버킷 소유 계정 ID를 지정합니다.
S3가 저장한 SHA-256과 크기, `image/webp` 형식이 일치하는 기존 파일은 다시 올리지 않습니다.
내용을 확인할 수 없거나 다른 파일은 덮어쓰지 않고 실패로 기록합니다.
새 파일은 `If-None-Match: *` 조건으로 업로드하므로 동시 실행 중에도 기존 파일을 덮어쓰지 않습니다.
업로드할 바이트를 다시 검사하고 S3의 체크섬을 재조회합니다.
AES256(SSE-S3), `Content-Type: image/webp`, `Cache-Control: public, max-age=86400`을 지정합니다.
같은 파일명을 계속 사용할 수 있으므로 장기간의 `immutable` 캐시는 지정하지 않습니다.

## 업로드 결과와 장소 연결

전체 사진의 업로드 또는 기존 파일 검증이 통과한 경우에만
`data/processed/place_photos_cloud.csv`를 임시 파일에서 원자적으로 교체합니다.
원본 `place_photos_web.csv`는 수정하지 않습니다.

| 컬럼 | 의미 |
| --- | --- |
| `photo_id`, `place_id`, `place_ids` | 사진 ID, 대표 장소 ID, 관련된 모든 장소 ID입니다. |
| `web_image_path`, `web_size_bytes`, `web_sha256` | 검증한 원본 웹용 목록의 경로, 크기, 내용 검사값입니다. |
| `s3_key`, `cdn_url` | S3 객체 경로와 사진을 표시할 HTTPS CloudFront URL입니다. |
| `upload_status`, `verified_at` | 자동 업로드는 `uploaded` 또는 `verified_existing`과 S3 검증 시각(UTC)입니다. 콘솔 업로드 후 주소만 생성하면 `url_generated`와 빈 시각입니다. |

장소별 연결에는 `place_ids`를 사용합니다. 시설명과 설명은 원본 CSV의 `sources`에서
해당 장소의 항목을 찾아 연결하며, 다른 장소의 설명을 합쳐서 표시하지 않습니다.
`unconfirmed` 항목은 확인된 시설 정보로 단정하지 않습니다.
현재 사진 API와 프론트는 이 CSV의 CloudFront 주소를 사용합니다.
주소 생성만으로 실제 원격 파일의 존재나 사진 의미의 정확성이 보장되는 것은 아닙니다.

부분 실패 또는 중단이 있으면 기존 연결 CSV를 유지합니다.
결과는 `outputs/photo_web/cloud_upload/upload-results.jsonl`에 사진별로 남습니다.
원본 제공기관 오류 메시지는 남기지 않고 오류 유형과 제한된 AWS 오류 코드만 기록합니다.
재실행하면 S3의 실제 체크섬을 다시 확인하므로 완료한 파일을 불필요하게 다시 업로드하지 않습니다.

## 검증 범위

```cmd
.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_photo_cloud_upload.py" -v
```

모의 S3 응답과 임시 사진을 사용해 크기·해시 불일치, 경로·ID 오류, 공유 장소 매핑,
기존 파일 검증, 충돌 방지, 부분 실패, 중단과 연결 CSV 보호를 확인합니다.
실제 AWS 업로드, OAC 접근, 브라우저 사진 표시는 수동 확인 대상입니다.

## 근거 문서

- [AWS CLI 설치](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- [콘솔 로그인으로 임시 자격 증명 사용](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)
- [Boto3 콘솔 자격 증명 지원](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html#login-with-console-credentials)
- [S3 조건부 업로드와 체크섬](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html)
- [S3 파일 메타데이터와 체크섬 조회](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html)
- [CloudFront OAC와 S3 읽기 정책](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html)
