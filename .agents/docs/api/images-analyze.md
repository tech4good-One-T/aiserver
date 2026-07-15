# 이미지 위험정보 분석 API

이미지 형식, 크기, 좌표계, Presigned URL과 오류 응답 형식은
[이미지 API 공통 명세](common.md)를 따른다.

## 개요

업로드할 이미지에서 딥페이크 악용 가능성, 개인정보 노출, 위치정보 노출을 분석한다.
위험 그룹별 위험도와 탐지된 영역의 좌표를 반환하며 이미지를 변경하거나 저장하지 않는다.

백엔드는 원본 이미지를 비공개 Object Storage에 먼저 저장하고, 짧은 만료 시간의 Presigned
GET URL을 발급해 AI 서버에 전달한다. AI 서버는 URL에서 이미지를 내려받아 분석한다.
서버는 EXIF 방향을 보정한 canonical PNG를 만든 뒤, 크기가 제한 이내면 그대로 또는
초과하면 메타데이터 없는 JPEG 전송본으로 Gemini 2.5 Flash API에 보내 구조화된 탐지
타입, 신뢰도, 문자와 0~1000 좌표를 받고 이를 픽셀 좌표로 변환한다. 전송본은 종횡비를
유지하며 해시·최종 결과에는 사용하지 않는다.
EXIF GPS의 실제 값은 Gemini에 보내지 않고 로컬에서 존재 여부만 검사한다.

백엔드는 응답의 `image.sha256`과 `detections`를 보관한 뒤, 사용자가 마스킹 대상으로 선택한
영역만 이미지 보호 처리 API에 전달한다. Presigned URL은 만료되는 임시 자격증명이므로
영구 저장하지 않고, 만료되지 않는 `source_object_key`를 이미지 식별자로 보관한다.

## 엔드포인트

```http
POST /api/v1/images/analyze
Content-Type: application/json
```

## 요청

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `source_object_key` | String | O | 원본 이미지의 Object Storage 객체 키 |
| `source_download_url` | String | O | 원본 다운로드용 Presigned GET URL |

`source_object_key`는 이미지 식별에만 사용하며 AI 서버가 버킷에 직접 접근하는 자격증명으로
사용하지 않는다. 실제 다운로드에는 `source_download_url`만 사용한다. AI 서버는 URL의 버킷과
경로가 `source_object_key` 및 서버에 설정된 허용 버킷과 일치하는지도 확인한다.

```json
{
  "source_object_key": "original/2026/07/image-123.jpg",
  "source_download_url": "https://example-bucket.s3.ap-northeast-2.amazonaws.com/original/2026/07/image-123.jpg?X-Amz-..."
}
```

다운로드된 이미지의 지원 형식은 정적 JPEG, PNG, WebP이며 최대 파일 크기는 10MB, 최대
해상도는 4096×4096으로 제한한다. GIF와 APNG·애니메이션 WebP는 첫 프레임만 사용하지
않고 `UNSUPPORTED_IMAGE_FORMAT`으로 거부한다. 다운로드 시 리다이렉트는 허용하지 않으며
응답 크기 제한을 스트리밍 중에도 적용한다.

```bash
curl -X POST \
  http://localhost:8000/api/v1/images/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "source_object_key":"original/2026/07/image-123.jpg",
    "source_download_url":"https://example-bucket.s3.ap-northeast-2.amazonaws.com/original/2026/07/image-123.jpg?X-Amz-..."
  }'
```

## 위험 그룹

응답은 다음 일곱 그룹을 모두 포함한다.

| `code` | 화면 표시 | 설명 |
| --- | --- | --- |
| `DEEPFAKE` | 딥페이크 위험 | 얼굴 노출로 인한 딥페이크 악용 가능성 |
| `IDENTITY` | 신원 식별 정보 | 신분증, 이름, 생년월일, 주민등록번호 등 |
| `VEHICLE` | 차량 정보 | 번호판, 주차 스티커, 차량 등록증 등 |
| `CONTACT_ACCOUNT` | 연락처 및 계정 정보 | 전화번호, 이메일, SNS 계정 등 |
| `RESIDENCE` | 주거지 정보 | 도로명·지번 주소, 아파트 동·호수 등 |
| `BUILDING` | 건물 식별 정보 | 아파트·학교·회사·상가명과 건물 번호 등 |
| `LOCATION` | 위치정보 위험 | EXIF GPS와 사진에서 유추 가능한 위치정보 |

위험도는 `HIGH`, `MEDIUM`, `LOW` 중 하나다. 한 그룹에서 여러 항목이 탐지되면 가장 높은
세부 위험도를 그룹 위험도로 사용한다. 탐지 결과가 없는 그룹도 응답에 포함하며
`detected=false`, `risk_level=LOW`, `detection_count=0`으로 반환한다.

## 성공 응답

```http
200 OK
Content-Type: application/json
```

```json
{
  "request_id": "req_01JZ123ABC",
  "source_object_key": "original/2026/07/image-123.jpg",
  "image": {
    "sha256": "8ac1e73c4c9f537f981f569312f3a15ab8674f7e50c696f8399d46bb52717532",
    "width": 1920,
    "height": 1080,
    "format": "jpeg",
    "orientation_normalized": true
  },
  "risk_groups": [
    {
      "code": "DEEPFAKE",
      "label": "딥페이크 위험",
      "detected": true,
      "risk_level": "HIGH",
      "detection_count": 1,
      "detection_ids": ["det_face_exposure_001"]
    },
    {
      "code": "IDENTITY",
      "label": "신원 식별 정보",
      "detected": false,
      "risk_level": "LOW",
      "detection_count": 0,
      "detection_ids": []
    },
    {
      "code": "VEHICLE",
      "label": "차량 정보",
      "detected": true,
      "risk_level": "HIGH",
      "detection_count": 1,
      "detection_ids": ["det_vehicle_license_plate_001"]
    },
    {
      "code": "CONTACT_ACCOUNT",
      "label": "연락처 및 계정 정보",
      "detected": false,
      "risk_level": "LOW",
      "detection_count": 0,
      "detection_ids": []
    },
    {
      "code": "RESIDENCE",
      "label": "주거지 정보",
      "detected": false,
      "risk_level": "LOW",
      "detection_count": 0,
      "detection_ids": []
    },
    {
      "code": "BUILDING",
      "label": "건물 식별 정보",
      "detected": true,
      "risk_level": "MEDIUM",
      "detection_count": 1,
      "detection_ids": ["det_company_name_001"]
    },
    {
      "code": "LOCATION",
      "label": "위치정보 위험",
      "detected": true,
      "risk_level": "HIGH",
      "detection_count": 1,
      "detection_ids": ["det_exif_gps_001"]
    }
  ],
  "detections": [
    {
      "id": "det_face_exposure_001",
      "risk_group": "DEEPFAKE",
      "type": "FACE_EXPOSURE",
      "label": "얼굴 노출",
      "confidence": 0.98,
      "detected_text": null,
      "region": {
        "bbox": {
          "x": 510,
          "y": 120,
          "width": 420,
          "height": 510
        },
        "polygon": [
          [510, 120],
          [930, 120],
          [930, 630],
          [510, 630]
        ]
      },
      "mask_supported": false,
      "processing_action": "APPLY_DEEPFAKE_PROTECTION"
    },
    {
      "id": "det_vehicle_license_plate_001",
      "risk_group": "VEHICLE",
      "type": "VEHICLE_LICENSE_PLATE",
      "label": "자동차 번호판",
      "confidence": 0.94,
      "detected_text": "12가3456",
      "region": {
        "bbox": {
          "x": 820,
          "y": 750,
          "width": 280,
          "height": 90
        },
        "polygon": [
          [820, 750],
          [1100, 750],
          [1100, 840],
          [820, 840]
        ]
      },
      "mask_supported": true,
      "processing_action": "MASK"
    },
    {
      "id": "det_company_name_001",
      "risk_group": "BUILDING",
      "type": "COMPANY_NAME",
      "label": "회사명",
      "confidence": 0.87,
      "detected_text": "Tech Innovators",
      "region": {
        "bbox": {
          "x": 1210,
          "y": 300,
          "width": 350,
          "height": 160
        },
        "polygon": [
          [1210, 300],
          [1560, 300],
          [1560, 460],
          [1210, 460]
        ]
      },
      "mask_supported": true,
      "processing_action": "MASK"
    },
    {
      "id": "det_exif_gps_001",
      "risk_group": "LOCATION",
      "type": "EXIF_GPS",
      "label": "GPS 위치정보",
      "confidence": 1.0,
      "detected_text": null,
      "region": null,
      "mask_supported": false,
      "processing_action": "REMOVE_METADATA"
    }
  ]
}
```

`bbox`와 `polygon`은 `image.orientation_normalized=true`인 이미지 기준 픽셀 좌표다. 원본에
EXIF 회전 정보가 있으면 방향을 먼저 보정한 뒤 해시와 좌표를 계산한다. `sha256`은
보정된 RGB 이미지의 정규화 PNG(`compress_level=6`) 바이트와 canonical metadata
fingerprint를 `0x00`으로 구분해 결합한 값의 64자리 SHA-256이다. fingerprint는 Orientation을
1로 정규화한 EXIF와 정렬한 Pillow `source.info` 항목을 포함하므로 픽셀이 같아도 해당
메타데이터가 다르면 해시가 다르다. 원본 JPEG·WebP 파일 바이트의 해시도 아니다.

## 탐지 결과 필드

| 필드 | 설명 |
| --- | --- |
| `id` | 한 분석 응답 안에서 탐지 항목을 식별하는 ID |
| `risk_group` | 탐지 항목이 속한 위험 그룹 |
| `type` | 세부 위험정보 종류 |
| `confidence` | 탐지 및 분류 신뢰도, 0 이상 1 이하 |
| `detected_text` | OCR로 인식한 문자열. 문자가 아니면 `null` |
| `region` | 이미지 안의 탐지 영역. 메타데이터 위험이면 `null` |
| `mask_supported` | 좌표 기반 마스킹 가능 여부 |
| `processing_action` | `MASK`, `APPLY_DEEPFAKE_PROTECTION`, `REMOVE_METADATA` 중 하나 |

백엔드와 프론트는 `mask_supported=true`인 항목만 사용자가 선택할 수 있는 블러 대상으로
노출한다. 딥페이크 위험은 선택 대상이 아니며 이미지 보호 처리 API가 항상 처리한다.

`confidence`는 Gemini의 시각 탐지·분류 신뢰도이다. 위험도는 Gemini에서 받지 않고
서버의 고정 타입별 정책으로 `HIGH`, `MEDIUM`, `LOW`를 결정한다.

## 세부 위험 타입

| 위험 그룹 | 타입 |
| --- | --- |
| `DEEPFAKE` | `FACE_EXPOSURE` |
| `IDENTITY` | `NATIONAL_ID_CARD`, `DRIVERS_LICENSE`, `PASSPORT` |
| `IDENTITY` | `STUDENT_ID_CARD`, `EMPLOYEE_ID_CARD`, `ACCESS_BADGE` |
| `IDENTITY` | `PERSON_NAME`, `DATE_OF_BIRTH`, `RESIDENT_REGISTRATION_NUMBER` |
| `IDENTITY` | `NAME_TAG`, `UNIFORM_REAL_NAME`, `SHIPPING_LABEL` |
| `VEHICLE` | `VEHICLE_LICENSE_PLATE`, `MOTORCYCLE_LICENSE_PLATE` |
| `VEHICLE` | `PARKING_STICKER`, `VEHICLE_REGISTRATION`, `PARKING_PASS` |
| `VEHICLE` | `VEHICLE_CONTACT_NUMBER` |
| `CONTACT_ACCOUNT` | `PHONE_NUMBER`, `EMAIL_ADDRESS`, `SNS_HANDLE` |
| `CONTACT_ACCOUNT` | `BUSINESS_CARD`, `SCREEN_USERNAME`, `PROFILE_INFORMATION` |
| `RESIDENCE` | `ROAD_NAME_ADDRESS`, `LOT_NUMBER_ADDRESS`, `APARTMENT_UNIT` |
| `BUILDING` | `APARTMENT_BRAND`, `BUILDING_NUMBER`, `BUILDING_NAME` |
| `BUILDING` | `STORE_NAME`, `SCHOOL_NAME`, `COMPANY_NAME` |
| `LOCATION` | `EXIF_GPS`, `VISUAL_LOCATION_CLUE`, `TRAVEL_ITINERARY` |

## 오류 응답

| HTTP 상태 | 오류 코드 | 조건 |
| --- | --- | --- |
| 400 | `INVALID_SOURCE_URL` | Presigned GET URL 형식이 잘못됨 |
| 400 | `CORRUPTED_IMAGE` | 이미지 디코딩 실패 |
| 403 | `SOURCE_URL_EXPIRED` | Presigned GET URL이 만료되거나 접근이 거부됨 |
| 413 | `IMAGE_TOO_LARGE` | 파일 크기 또는 해상도 제한 초과 |
| 415 | `UNSUPPORTED_IMAGE_FORMAT` | 지원하지 않는 이미지 형식 |
| 500 | `IMAGE_ANALYSIS_FAILED` | 분석 중 알 수 없는 오류 발생 |
| 502 | `SOURCE_DOWNLOAD_FAILED` | 원본 이미지 다운로드 실패 |
| 503 | `ANALYSIS_MODEL_UNAVAILABLE` | 필요한 분석 모델을 사용할 수 없음 |
| 504 | `ANALYSIS_TIMEOUT` | 분석 제한 시간 초과 |

```json
{
  "error": {
    "code": "UNSUPPORTED_IMAGE_FORMAT",
    "message": "지원하지 않는 이미지 형식입니다.",
    "request_id": "req_01JZ123ABC"
  }
}
```

## 개인정보 및 보관 정책

- AI 서버는 입력 이미지와 탐지된 개인정보 원문을 영구 저장하지 않는다.
- Gemini 전송은 크기 제한에 따라 메타데이터 없는 정규화 PNG 또는 JPEG 전송본이며,
  이미지에 보이는 얼굴·문서·문자 등의 픽셀 정보는 분석을 위해 Google Gemini API에 전송된다.
- 실제 개인정보를 처리하기 전에 이용자 고지·동의와 사용 계정의 현행 데이터
  보관·모델 개선 사용·삭제 조건을 별도로 검토한다.
- 이미지, OCR 원문, EXIF 값은 로그에 기록하지 않는다.
- Presigned URL에는 임시 서명이 포함되므로 어떤 로그 수준에서도 기록하지 않는다.
- 다운로드 URL은 HTTPS만 허용하고 설정된 S3 버킷 호스트만 허용 목록으로 검증한다.
- URL 리다이렉트를 허용하지 않아 내부 주소 접근과 SSRF를 방지한다.
- GPS가 탐지되어도 실제 위도·경도는 응답하지 않는다.
- `request_id`, 처리 시간, 모델 상태와 같은 비식별 운영 정보만 로그에 기록한다.
