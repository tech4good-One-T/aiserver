# 서비스 전체 개요

## 1. 서비스 한 줄 설명

사용자가 SNS나 웹에 이미지를 게시하기 전에 이미지 속 개인정보와 위치정보 위험을 찾아 알려주고, 선택한 위험 영역을 블러 처리하며, 얼굴이 포함된 경우 딥페이크 방어 처리를 적용해 안전한 PNG를 만들어 주는 AI 이미지 보호 서비스다.

이 저장소(`aiserver1`)는 프론트엔드와 별도로 동작하는 FastAPI 기반 AI 서버다.

## 2. 해결하려는 문제

사진 한 장에는 사용자가 의도하지 않은 정보가 함께 포함될 수 있다.

- 얼굴이 그대로 노출되어 딥페이크 학습·합성에 악용될 수 있음
- 주민등록증, 여권, 학생증, 사원증 등이 촬영될 수 있음
- 자동차 번호판, 주차 스티커, 차량 연락처가 보일 수 있음
- 전화번호, 이메일, SNS 아이디, 명함이 노출될 수 있음
- 주소, 아파트 동·호수, 건물명으로 거주지나 생활 반경이 추정될 수 있음
- 사진의 EXIF 메타데이터에 GPS 위치가 남을 수 있음

서비스는 위험정보를 자동으로 찾아 사용자에게 보여주고, 사용자가 선택한 항목만 마스킹한다. 딥페이크 방어는 얼굴이 발견되면 선택 옵션 없이 필수로 수행한다.

## 3. 주요 사용자와 사용 시나리오

### 대상 사용자

- SNS에 일상 사진을 게시하는 일반 사용자
- 연예인·인플루언서 등 얼굴 도용 위험이 높은 사용자
- 자녀 사진을 게시하는 보호자
- 사내·학교·행사 사진을 공유하는 조직

### 대표 시나리오

1. 사용자가 사진을 선택한다.
2. 백엔드가 원본을 저장하고 presigned URL을 발급한다.
3. AI 서버가 URL로 이미지를 받아 위험 유형·위험도·좌표를 분석한다.
4. 프론트엔드가 분석 결과를 사용자에게 표시한다.
5. 사용자가 블러할 개인정보 영역을 선택한다.
6. AI 서버가 선택 영역을 OpenCV로 블러 처리한다.
7. 얼굴이 있으면 FaceShield를 반드시 실행해 얼굴 보호 섭동을 적용한다.
8. EXIF 제거 옵션에 따라 메타데이터를 제거한 PNG를 S3에 업로드한다.
9. 백엔드가 결과 URL을 프론트엔드에 전달한다.

## 4. 위험정보 분류

Gemini는 이미지 픽셀에서 시각적으로 확인 가능한 위험정보와 좌표를 반환한다. EXIF GPS 여부는 AI 추측이 아니라 AI 서버가 직접 검사한다.

### 위험 그룹

| 그룹 코드 | 내용 | 예시 |
| --- | --- | --- |
| `DEEPFAKE` | 얼굴 노출 및 딥페이크 위험 | 사람 얼굴 |
| `IDENTITY` | 신원 식별 정보 | 주민등록증, 여권, 학생증, 이름, 생년월일, 명찰 |
| `VEHICLE` | 차량 정보 | 번호판, 주차 스티커, 차량 등록증, 차량 연락처 |
| `CONTACT_ACCOUNT` | 연락처·계정 정보 | 전화번호, 이메일, SNS 아이디, 명함 |
| `RESIDENCE` | 주거지 정보 | 도로명 주소, 지번 주소, 아파트 동·호수 |
| `BUILDING` | 건물 식별 정보 | 아파트 브랜드, 건물명, 학교명, 회사명 |
| `LOCATION` | 위치·일정 정보 | GPS 메타데이터, 공항·카페·여행 일정 단서 |

각 위험 그룹과 세부 탐지 항목에는 `HIGH`, `MEDIUM`, `LOW` 위험도가 포함될 수 있다. 탐지 결과에는 사용자 선택 처리를 위한 고유 detection ID와 EXIF 회전 보정 후의 이미지 픽셀 좌표가 포함된다.

## 5. API 구성

Base URL은 `/api/v1`이다.

### `POST /api/v1/images/analyze`

이미지를 분석하고 위험 그룹, 탐지 항목, 위험도, 좌표, 이미지 해시를 반환한다.

요청 본문:

```json
{
  "source_object_key": "original/example.png",
  "source_download_url": "<HTTPS presigned GET URL>"
}
```

주요 응답 내용:

- 이미지 형식·너비·높이
- 분석 이미지 SHA-256
- GPS EXIF 존재 여부
- 위험 그룹별 위험도
- 탐지 항목의 `detection_id`, 유형, 신뢰도, 좌표

### `POST /api/v1/images/process`

원본을 다시 다운로드해 해시를 검증하고, 선택한 영역을 블러 처리한 뒤 얼굴 보호와 메타데이터 처리를 수행한다.

요청에는 다음 정보가 포함된다.

- 원본 객체 키와 presigned GET URL
- 결과 객체 키와 presigned PUT URL
- `/analyze` 응답에서 받은 `analysis_image_sha256`
- 사용자가 선택한 마스킹 영역 목록
- `remove_metadata` 옵션

`DEEPFAKE` 영역은 `selected_regions`에 넣을 수 없다. 얼굴이 감지되면 FaceShield 보호가 자동으로 적용된다.

응답에는 마스킹 영역 수, 딥페이크 보호 적용 여부, 메타데이터 제거 여부, 결과 객체 키가 포함된다.

상세 계약은 다음 문서를 참고한다.

- [공통 API 규칙](./api/common.md)
- [이미지 분석 API](./api/images-analyze.md)
- [이미지 처리 API](./api/images-process.md)

## 6. 시스템 구성

```text
프론트엔드
    │ 분석/처리 요청
    ▼
백엔드
    ├─ S3 원본 저장 및 presigned URL 발급
    └─ AI 서버 API 호출
          │ HTTPS presigned GET
          ▼
AI 서버(FastAPI)
    ├─ Image Gateway: URL 검증·다운로드·결과 업로드
    ├─ Pillow: 형식 검증·EXIF 회전 보정·정규화
    ├─ Gemini API: 위험정보·좌표 분석
    ├─ EXIF 검사: GPS 메타데이터 확인
    ├─ OpenCV: 선택 영역 블러
    └─ FaceShield CLI: 얼굴 딥페이크 방어
          │ HTTPS presigned PUT
          ▼
S3 결과 객체
```

AI 서버는 원본 이미지를 영구 저장하지 않는다. 요청 처리 중 임시 파일과 메모리만 사용하고, 결과는 백엔드가 제공한 presigned PUT URL로 S3에 직접 업로드한다.

## 7. AI·이미지 처리 구성

### Gemini

- 이미지의 시각적 위험정보를 구조화된 JSON으로 반환
- 허용된 위험 그룹과 세부 유형만 서버 스키마로 검증
- 좌표는 0~1000 정규화 좌표로 받고 실제 이미지 픽셀 좌표로 변환
- 서버가 전송하는 이미지는 메타데이터가 제거된 정규화 이미지

### Pillow

- JPEG, PNG, WebP만 허용
- EXIF 회전을 먼저 적용해 좌표 기준을 통일
- 최대 10MB, 최대 4096×4096 해상도
- 처리용 정규화 PNG와 SHA-256 생성

### OpenCV

- 사용자가 선택한 `IDENTITY`, `VEHICLE`, `CONTACT_ACCOUNT`, `RESIDENCE`, `BUILDING`, `LOCATION` 영역만 블러
- 분석 결과와 처리 요청의 SHA-256이 다르면 처리를 중단

### FaceShield

- 얼굴이 존재할 때만 실행하지만, 얼굴 보호 자체는 항상 적용
- 현재 저장소에는 공식 CLI를 호출하는 adapter가 구현되어 있음
- FaceShield 저장소·conda 환경·CUDA·GPU·가중치는 외부 런타임 전제조건
- 보호 실패 시 보호되지 않은 결과를 업로드하지 않고 오류를 반환

FaceShield 설치·호스트 구성은 [배포 문서](./deployment.md)와 [AI 아키텍처](./ai_architect.md)를 참고한다.

## 8. 데이터와 보안 원칙

- AI 서버는 원본 이미지를 영구 저장하지 않는다.
- 원본과 결과는 백엔드가 관리하는 S3 객체로 제한한다.
- AI 서버는 HTTPS presigned URL만 허용한다.
- 허용된 S3 호스트와 객체 경로를 검증한다.
- URL에 자격 증명이나 fragment가 포함되면 거부한다.
- 분석·처리 이미지의 SHA-256을 비교해 이미지 교체를 방지한다.
- `GEMINI_API_KEY`, AWS 자격 증명, presigned URL은 Git·로그·메신저에 남기지 않는다.
- Gemini 사용 시 이미지 픽셀이 외부 API로 전송되므로 개인정보 처리 고지·동의와 제공자 데이터 처리 조건을 확인해야 한다.
- 결과 기본 형식은 PNG이며 `remove_metadata=true`이면 EXIF를 제거한다.

## 9. 실행 모드와 제한사항

### CPU 개발 모드

FastAPI, Pillow, OpenCV, Gemini 분석은 CPU 환경에서 실행할 수 있다. FaceShield가 설치되지 않은 상태에서 얼굴이 있는 처리 요청은 성공할 수 없다.

### 전체 기능 배포 모드

- Ubuntu 22.04 기반 GPU EC2 권장
- FastAPI는 Python 3.12/uv 프로세스로 실행
- FaceShield는 별도 Python 3.8/CUDA conda 환경의 CLI로 실행
- `FACESHIELD_REPO_PATH`와 `FACESHIELD_COMMAND`로 adapter 연결
- Docker 이미지는 현재 CPU API 런타임이며 FaceShield CUDA 환경·가중치를 포함하지 않는다.

### 현재 범위의 제한

- 이미지-투-동영상 보호는 범위에 포함하지 않는다.
- FaceShield는 얼굴 크롭마다 CLI 프로세스를 실행하므로 여러 얼굴에서는 처리 시간이 증가한다.
- 생성 모델과 방어 모델의 발전에 따라 FaceShield 방어 효과를 지속적으로 재검증해야 한다.
- 모델 API나 FaceShield 런타임이 unavailable이면 안전하지 않은 결과를 반환하지 않는다.

## 10. 저장소 구조

```text
aiserver1/
├─ app/api/                 # FastAPI 라우트·의존성·스키마
├─ app/core/                # 설정·오류·요청 로깅
├─ app/services/            # Gemini·S3·이미지·위험정책·FaceShield
├─ tests/                   # 단위·API·계약 테스트
├─ .agents/docs/api/        # API 상세 명세
├─ .agents/docs/ai_architect.md
├─ .agents/docs/deployment.md
├─ Dockerfile               # CPU FastAPI 런타임
└─ pyproject.toml           # uv 의존성·개발 도구
```

## 11. 검증 기준

로컬 정적·단위 검증:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

S3 연동 검증 시에는 다음을 확인한다.

1. `/health`가 `200`을 반환하는지
2. presigned GET으로 원본 다운로드가 되는지
3. `/analyze`가 위험정보와 SHA-256을 반환하는지
4. `/process`가 선택 영역 블러와 얼굴 보호를 수행하는지
5. presigned PUT 결과 객체가 S3에 생성되는지

모델 오류, S3 오류, FaceShield/CUDA 오류는 서로 구분해 기록한다.
