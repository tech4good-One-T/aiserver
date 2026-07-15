# 이미지 보호 처리 API

이미지 형식, 크기, 좌표계, Presigned URL과 오류 응답 형식은
[이미지 API 공통 명세](common.md)를 따른다.

## 개요

이미지 위험정보 분석 API가 반환한 좌표 중 사용자가 선택한 영역만 블러하고, 얼굴이 있으면
딥페이크 방지 처리를 항상 적용한다. 메타데이터 제거 여부는 별도 옵션으로 전달받는다.

이 API는 `/analyze`의 서버 상태에 의존하지 않는다. 대신 FaceShield를 실행할 얼굴이 있는지
확인하기 위해 크기 제한을 지킨 메타데이터 없는 PNG 또는 JPEG 전송본을 Gemini 2.5 Flash API에
다시 전송한다. `/analyze` 후
`/process`를 호출하면 외부 이미지 분석 호출은 총 2회다.

AI 서버는 분석 상태를 저장하지 않는다. 백엔드는 분석 응답의 이미지 SHA-256과 선택된 좌표를
보관한다. 처리 요청 전에 원본 다운로드용 Presigned GET URL과 결과 업로드용 Presigned PUT
URL을 발급해 객체 키, 이미지 해시, 선택 좌표와 함께 이 API에 전달해야 한다.

## 엔드포인트

```http
POST /api/v1/images/process
Content-Type: application/json
```

## 요청

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `source_object_key` | String | O | 분석 API에 사용한 원본 객체 키 |
| `source_download_url` | String | O | 원본 다운로드용 Presigned GET URL |
| `result_object_key` | String | O | 보호 이미지가 저장될 객체 키 |
| `result_upload_url` | String | O | 결과 업로드용 Presigned PUT URL |
| `result_content_type` | String | O | 결과 이미지 Content-Type. 현재 `image/png`만 허용 |
| `analysis_image_sha256` | String | O | 분석 응답의 `image.sha256` 값 |
| `selected_regions` | Array | O | 사용자가 블러 대상으로 선택한 좌표 목록. 최대 100개 |
| `remove_metadata` | Boolean | O | 원본 EXIF 복사 여부. `true`면 EXIF를 복사하지 않음 |

딥페이크 방지는 필수 처리이므로 활성화 여부를 요청 필드로 받지 않는다.

### 요청 본문

```json
{
  "source_object_key": "original/2026/07/image-123.jpg",
  "source_download_url": "https://example-bucket.s3.ap-northeast-2.amazonaws.com/original/2026/07/image-123.jpg?X-Amz-...",
  "result_object_key": "protected/2026/07/image-123.png",
  "result_upload_url": "https://example-bucket.s3.ap-northeast-2.amazonaws.com/protected/2026/07/image-123.png?X-Amz-...",
  "result_content_type": "image/png",
  "analysis_image_sha256": "8ac1e73c4c9f537f981f569312f3a15ab8674f7e50c696f8399d46bb52717532",
  "selected_regions": [
    {
      "detection_id": "det_vehicle_license_plate_001",
      "risk_group": "VEHICLE",
      "polygon": [
        [820, 750],
        [1100, 750],
        [1100, 840],
        [820, 840]
      ]
    },
    {
      "detection_id": "det_company_name_001",
      "risk_group": "BUILDING",
      "polygon": [
        [1210, 300],
        [1560, 300],
        [1560, 460],
        [1210, 460]
      ]
    }
  ],
  "remove_metadata": true
}
```

사용자가 마스킹 항목을 하나도 선택하지 않은 경우 빈 배열 `[]`을 전달한다. 좌표는 분석
응답에서 받은 값을 임의로 변환하지 않고 그대로 전달한다. 각 polygon은 3~128개의
고유한 점으로 구성해야 한다.

AI 서버는 별도 분석 상태를 보관하지 않으므로 `detection_id`와 polygon을 이전
`/analyze` 응답과 다시 대조하지 않는다. 백엔드가 원본 분석 응답의 값을 위변조 없이
보관해 전달해야 하며, AI 서버는 해시와 polygon 형태·경계를 검증한다.

```bash
curl -X POST \
  http://localhost:8000/api/v1/images/process \
  -H "Content-Type: application/json" \
  -d '{
    "source_object_key":"original/2026/07/image-123.jpg",
    "source_download_url":"https://example-bucket.s3.ap-northeast-2.amazonaws.com/original/2026/07/image-123.jpg?X-Amz-...",
    "result_object_key":"protected/2026/07/image-123.png",
    "result_upload_url":"https://example-bucket.s3.ap-northeast-2.amazonaws.com/protected/2026/07/image-123.png?X-Amz-...",
    "result_content_type":"image/png",
    "analysis_image_sha256":"8ac1e73c4c9f537f981f569312f3a15ab8674f7e50c696f8399d46bb52717532",
    "selected_regions":[
      {
        "detection_id":"det_vehicle_license_plate_001",
        "risk_group":"VEHICLE",
        "polygon":[[820,750],[1100,750],[1100,840],[820,840]]
      }
    ],
    "remove_metadata":true
  }'
```

## 입력 검증

1. 두 Presigned URL이 HTTPS이고 허용된 S3 버킷 호스트인지 확인한다.
2. 각 URL의 버킷과 경로가 대응하는 `source_object_key`, `result_object_key`와 일치하는지 확인한다.
3. `source_download_url`로 원본을 내려받고 EXIF 방향에 맞게 회전 보정한다.
4. 보정된 RGB 이미지의 정규화 PNG(`compress_level=6`) 바이트와 canonical metadata
   fingerprint를 `0x00`으로 구분해 결합한 값의 SHA-256이 `analysis_image_sha256`과
   일치하는지 확인한다. fingerprint는 Orientation을 1로 정규화한 EXIF와 정렬한
   Pillow `source.info` 항목을 포함한다.
5. `selected_regions`의 각 점이 이미지 범위 안에 있는지 확인한다.
6. 선택 영역이 100개 이하이고, 각 다각형이 3~128개의 고유한 점으로 구성되며
   자기 교차 등 처리 불가능한 형태가 아닌지 확인한다.
7. 중복되거나 겹치는 선택 영역은 병합한 뒤 처리한다.
8. `result_content_type`이 Presigned PUT 생성 시 서명한 Content-Type과 일치하는지 확인한다.

해시가 다르면 다른 이미지의 좌표가 전달된 것이므로 처리를 거부한다.

## 처리 순서

1. `source_download_url`에서 원본 이미지를 다운로드한다.
2. 이미지 형식과 크기를 검증하고 EXIF 방향을 보정한다.
3. 정규화 PNG와 canonical metadata fingerprint를 결합한 SHA-256을
   `analysis_image_sha256`과 비교한다.
4. 크기 제한을 지킨 메타데이터 없는 PNG 또는 JPEG 전송본을 Gemini API에 전송해
   `FACE_EXPOSURE` 존재 여부를 재확인한다.
5. 전달받은 선택 polygon의 합집에만 OpenCV Gaussian blur를 적용한다.
6. 얼굴이 있으면 각 Gemini 얼굴 bbox에 `max(16px, 긴 변의 20%)` 여백을 더해 블러
   결과에서 크롭한다.
7. 얼굴 크롭별로 별도 conda/CUDA 환경의 FaceShield 공식 CLI를 필수 호출한다.
   CLI가 512 근처로 크롭을 줄여 반환하면 `protected_small - clean_small`로 보호
   delta를 계산하고 delta만 bilinear 보간으로 원래 크롭 크기에 매핑한다.
8. 고해상도 원본 크롭에 매핑한 delta를 더한 결과를 원래 좌표에 합성해 전체
   사진의 해상도, 고해상도 상세와 좌표계를 유지한다.
9. `remove_metadata=true`면 EXIF를 제거하고, `false`면 회전 값만 정규화한 EXIF를
   보존해 최종 결과를 PNG로 인코딩한다.
10. `result_upload_url`에 `Content-Type: image/png`으로 결과를 PUT한다.
11. 업로드가 성공한 뒤 처리 결과 JSON을 반환한다.

딥페이크 방지는 항상 시도한다. 얼굴이 없는 이미지는 적용할 대상이 없으므로 해당 단계만
건너뛰고 정상 응답한다. 얼굴이 탐지됐지만 방지 모델 실행이 실패하면 보호되지 않은 이미지를
반환하지 않고 오류로 처리한다.

현재 FaceShield 공식 CLI 자체는 bbox 인자를 받지 않으므로 AI 서버가 Gemini bbox로
얼굴 크롭을 만들어 CLI에 이미지 입력으로 전달한다. 다중 얼굴은 크롭별로 순차
호출하므로 단일 얼굴보다 처리 시간이 길어진다. 크롭 중 하나라도 FaceShield가 실패하면
결과 전체를 업로드하지 않는다.

보호 delta만 매핑해 전체 사진 해상도와 원본 크롭 상세를 유지하지만, 작은
해상도의 delta를 bilinear로 키우는 과정이 FaceShield 섭동을 약화할 수 있으므로 실제
딥페이크 모델과 SNS 후처리 경로에서 별도 검증한다. FaceShield 출력의
가로세로 비율 차이가 2%를 넘으면 안전하게 합성할 수 없으므로 처리를 실패로 종료한다.

`remove_metadata=false`면 GPS를 포함한 원본 EXIF가 결과에 남을 수 있으므로 개인정보 보호
기본값으로는 `true`를 권장한다.

`false`일 때도 보존하는 범위는 회전 값을 1로 정규화한 EXIF뿐이다. XMP, IPTC, ICC
프로파일 등은 Pillow `source.info`에 노출되면 변경 감지용 fingerprint에는 포함될 수
있지만, 위험을 분석하거나 최종 PNG에 보존하지 않는다. 즉
`false`를 모든 원본 메타데이터 보존으로 해석하면 안 된다.

## 성공 응답

```http
200 OK
Content-Type: application/json
```

```json
{
  "request_id": "req_01JZ123ABC",
  "status": "COMPLETED",
  "source_object_key": "original/2026/07/image-123.jpg",
  "result_object_key": "protected/2026/07/image-123.png",
  "result_content_type": "image/png",
  "masked_region_count": 2,
  "deepfake_protection": {
    "attempted": true,
    "applied": true,
    "skip_reason": null
  },
  "metadata_removed": true
}
```

백엔드는 만료되는 `result_upload_url`이 아니라 `result_object_key`를 영구 저장한다. 사용자에게
결과 이미지를 보여줄 때는 백엔드가 별도의 Presigned GET URL을 발급한다.

얼굴이 없으면 `deepfake_protection.applied=false`,
`deepfake_protection.skip_reason=NO_FACE_DETECTED`로 반환한다.

## 오류 응답

| HTTP 상태 | 오류 코드 | 조건 |
| --- | --- | --- |
| 400 | `INVALID_SOURCE_URL` | Presigned GET URL 형식이 잘못됨 |
| 400 | `INVALID_RESULT_URL` | Presigned PUT URL 형식이 잘못됨 |
| 400 | `INVALID_SELECTED_REGIONS` | `selected_regions` 형식 오류 |
| 400 | `CORRUPTED_IMAGE` | 이미지 디코딩 실패 |
| 403 | `SOURCE_URL_EXPIRED` | Presigned GET URL이 만료되거나 접근이 거부됨 |
| 403 | `RESULT_URL_EXPIRED` | Presigned PUT URL이 만료되거나 접근이 거부됨 |
| 409 | `IMAGE_ANALYSIS_MISMATCH` | 분석 이미지와 처리 이미지의 해시가 다름 |
| 413 | `IMAGE_TOO_LARGE` | 파일 크기 또는 해상도 제한 초과 |
| 415 | `UNSUPPORTED_IMAGE_FORMAT` | 지원하지 않는 이미지 형식 |
| 422 | `RESULT_CONTENT_TYPE_MISMATCH` | 결과 Content-Type이 PUT URL의 서명 조건과 다름 |
| 422 | `INVALID_REGION` | 점 개수나 다각형 형태가 잘못됨 |
| 422 | `REGION_OUT_OF_BOUNDS` | 좌표가 이미지 범위를 벗어남 |
| 500 | `IMAGE_PROCESSING_FAILED` | 이미지 처리 중 알 수 없는 오류 발생 |
| 502 | `SOURCE_DOWNLOAD_FAILED` | 원본 이미지 다운로드 실패 |
| 502 | `RESULT_UPLOAD_FAILED` | 보호 이미지 업로드 실패 |
| 503 | `DEEPFAKE_PROTECTION_FAILED` | 얼굴이 있으나 필수 딥페이크 방지 처리 실패 |
| 504 | `PROCESSING_TIMEOUT` | 전체 처리 제한 시간 초과 |

```json
{
  "error": {
    "code": "DEEPFAKE_PROTECTION_FAILED",
    "message": "딥페이크 방지 처리에 실패했습니다.",
    "request_id": "req_01JZ123ABC"
  }
}
```

## 개인정보 및 보관 정책

- AI 서버는 원본과 결과 이미지를 영구 저장하지 않는다.
- 얼굴 재확인을 위해 메타데이터 없는 정규화 PNG 또는 크기 제한용 JPEG 전송본의 픽셀이
  Google Gemini API에 전송된다. EXIF는 해당 전송본에 포함되지 않지만 픽셀에 보이는
  개인정보는 외부 처리 대상이다.
- 실제 개인정보 이미지를 처리하기 전에 이용자 고지·동의와 사용 계정의 현행
  데이터 보관·삭제·모델 개선 사용 조건을 별도로 검토한다.
- 이미지 내용, 선택 좌표에 연결된 개인정보 원문과 EXIF 값을 로그에 기록하지 않는다.
- Presigned URL에는 임시 서명이 포함되므로 어떤 로그 수준에서도 기록하지 않는다.
- 다운로드와 업로드 URL은 HTTPS만 허용하고 설정된 S3 버킷 호스트만 허용 목록으로 검증한다.
- URL 리다이렉트를 허용하지 않아 내부 주소 접근과 SSRF를 방지한다.
- 결과 이미지는 메모리 또는 요청 단위 임시 파일에서 업로드한 뒤 즉시 폐기한다.
- `request_id`, 처리 시간, 선택 영역 개수와 단계별 성공 여부만 로그에 기록한다.
