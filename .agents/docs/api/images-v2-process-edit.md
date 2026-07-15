# v2 이미지 처리 및 프롬프트 기반 후처리 API

이미지 형식, 크기, Presigned URL, 요청 추적과 공통 오류 형식은 별도 언급이 없는 한
[이미지 API 공통 명세](common.md)를 따른다. 기존 `/api/v1` 계약은 유지한다.

## 목표

v2는 다음 두 단계를 제공한다.

1. **Step 2 — 버전형 개인정보 보호 처리**
   - 선택 영역 마스킹과 FaceShield 얼굴 보호를 수행한다.
   - 결과 해시를 반환해 이후 편집의 기준 이미지를 검증할 수 있게 한다.
2. **Step 3 — 프롬프트 기반 후처리**
   - 사용자가 Step 2 또는 이전 편집 결과에 추가 편집 프롬프트를 입력한다.
   - LangChain과 Gemini가 이미지를 재가공한다.
   - 편집 결과를 다시 분석하고 개인정보 마스킹과 FaceShield를 재적용한다.

프롬프트 기반 재가공 자체가 v2에서 추가되는 후처리다. 별도의 추가 후처리 알고리즘은 없다.

## 시스템 경계

### 공개 Backend API

프론트엔드는 AI 서버를 직접 호출하지 않는다. 업로드와 최초 분석은 기존 v1 API를 재사용하고,
버전형 결과 생성부터 v2 API를 사용한다.

```text
Step 1  POST /api/v1/images/upload
        GET  /api/v1/images/{task_id}/status

Step 2  POST /api/v2/images/{task_id}/process

Step 3  POST /api/v2/images/{task_id}/edits

공통    GET  /api/v2/image-operations/{operation_id}
```

Backend는 Celery 작업, 결과 버전, 부모 결과와 S3 객체 키를 PostgreSQL에 저장한다.

### AI Server 내부 API

```text
POST /api/v2/images/process
POST /api/v2/images/edit
```

AI 서버는 상태나 이미지를 영구 저장하지 않는다. Backend worker가 객체 키와 짧은 만료 시간의
Presigned URL을 매 요청마다 전달하고, AI 서버는 최종 결과만 업로드한다.

## 모델 구성

| 역할 | 모델 | 연동 방식 |
| --- | --- | --- |
| 개인정보 위험 분석 | `gemini-3.5-flash` | 기존 Gemini analyzer |
| 프롬프트 정책 검증·정규화 | `gemini-3.5-flash` | LangChain 구조화 출력 |
| 실제 이미지 편집 | `gemini-3.1-flash-image` | LangChain 멀티모달 이미지 출력 |
| 얼굴 딥페이크 방지 | FaceShield | 별도 GPU CLI |

`gemini-3.5-flash`는 이미지 편집 결과를 생성하지 않으므로 실제 이미지 생성·편집에는
`gemini-3.1-flash-image`를 사용한다.

## 공통 원칙

- v1 API와 결과를 덮어쓰지 않는다.
- 결과 객체 키는 `protected/v2/{task_id}/{output_id}.png` 형식을 사용한다.
- 결과는 정적 PNG이며 EXIF를 포함하지 않는다.
- Presigned URL, 사용자 프롬프트, 이미지/OCR 원문은 로그에 기록하지 않는다.
- 성공 응답에는 Presigned URL이 아닌 객체 키와 결과 이미지 해시를 포함한다.
- 이미지 해시는 [공통 해시 규칙](common.md#정규화-이미지와-해시)을 사용한다.
- 프롬프트 편집 뒤 개인정보 보호 재처리는 필수이며 끌 수 없다.
- 중간 생성 이미지는 업로드하거나 사용자에게 반환하지 않는다.

---

## Step 2 — 버전형 개인정보 보호 처리

### AI Server 엔드포인트

```http
POST /api/v2/images/process
Content-Type: application/json
```

요청 구조는 v1 `/api/v1/images/process`와 같지만 `remove_metadata`는 반드시 `true`다.

```json
{
  "source_object_key": "uploads/a1b2c3d4.jpg",
  "source_download_url": "https://example-bucket.s3.ap-northeast-2.amazonaws.com/uploads/a1b2c3d4.jpg?X-Amz-...",
  "result_object_key": "protected/v2/a1b2c3d4/out_01JZ123ABC.png",
  "result_upload_url": "https://example-bucket.s3.ap-northeast-2.amazonaws.com/protected/v2/a1b2c3d4/out_01JZ123ABC.png?X-Amz-...",
  "result_content_type": "image/png",
  "analysis_image_sha256": "8ac1e73c4c9f537f981f569312f3a15ab8674f7e50c696f8399d46bb52717532",
  "selected_regions": [
    {
      "detection_id": "det_vehicle_license_plate_001",
      "risk_group": "VEHICLE",
      "polygon": [[820, 750], [1100, 750], [1100, 840], [820, 840]]
    }
  ],
  "remove_metadata": true
}
```

### 처리 순서

1. 원본 다운로드와 이미지 형식·크기·URL을 검증한다.
2. `analysis_image_sha256`으로 분석 이미지와 같은 원본인지 확인한다.
3. 얼굴을 재탐지한다.
4. 선택 영역을 마스킹한다.
5. 탐지된 얼굴에 FaceShield를 적용한다.
6. 메타데이터가 없는 PNG로 인코딩한다.
7. 결과 해시를 계산하고 최종 이미지만 업로드한다.

### 성공 응답

```json
{
  "request_id": "req_01JZ123ABC",
  "status": "COMPLETED",
  "source_object_key": "uploads/a1b2c3d4.jpg",
  "result_object_key": "protected/v2/a1b2c3d4/out_01JZ123ABC.png",
  "result_content_type": "image/png",
  "result_image_sha256": "9bc2f84d5d0e648a092fc67af41a26bc09785f806ad10a123569fd2d434ff310",
  "masked_region_count": 1,
  "deepfake_protection": {
    "attempted": true,
    "applied": true,
    "skip_reason": null
  },
  "metadata_removed": true
}
```

Backend는 객체 키와 결과 해시를 `image_outputs`에 저장한다.

---

## Step 3 — 프롬프트 기반 후처리

### AI Server 엔드포인트

```http
POST /api/v2/images/edit
Content-Type: application/json
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `source_object_key` | String | O | Step 2 또는 이전 편집 결과 객체 키 |
| `source_download_url` | String | O | 기준 이미지 Presigned GET URL |
| `result_object_key` | String | O | 새 결과 객체 키 |
| `result_upload_url` | String | O | 새 결과 Presigned PUT URL |
| `result_content_type` | String | O | `image/png`만 허용 |
| `source_image_sha256` | String | O | 기준 output의 결과 이미지 해시 |
| `prompt` | String | O | 1~2000자의 이미지 편집 요청 |
| `remove_metadata` | Boolean | O | 반드시 `true` |

```json
{
  "source_object_key": "protected/v2/a1b2c3d4/out_01JZ123ABC.png",
  "source_download_url": "https://example-bucket.s3.ap-northeast-2.amazonaws.com/protected/v2/a1b2c3d4/out_01JZ123ABC.png?X-Amz-...",
  "result_object_key": "protected/v2/a1b2c3d4/out_01JZ456DEF.png",
  "result_upload_url": "https://example-bucket.s3.ap-northeast-2.amazonaws.com/protected/v2/a1b2c3d4/out_01JZ456DEF.png?X-Amz-...",
  "result_content_type": "image/png",
  "source_image_sha256": "9bc2f84d5d0e648a092fc67af41a26bc09785f806ad10a123569fd2d434ff310",
  "prompt": "배경을 자연스럽게 정리하고 전체적으로 따뜻한 분위기로 만들어줘",
  "remove_metadata": true
}
```

### LangChain 처리

1. `gemini-3.5-flash`가 사용자 요청을 Pydantic `EditPlan`으로 구조화한다.
2. 개인정보 복원·블러 해제·얼굴 보호 제거 요청이면 `PROMPT_NOT_ALLOWED`로 거부한다.
3. 허용된 정규화 instruction과 기준 이미지를 `gemini-3.1-flash-image`에 전달한다.
4. 이미지 응답 블록에서 유효한 PNG 또는 JPEG 한 장만 추출한다.

LangChain에는 파일시스템, 셸, 임의 네트워크 요청 도구를 제공하지 않는다.

### 개인정보 보호 재처리

이미지 편집 모델이 기존 마스킹을 복원하거나 새 개인정보를 생성할 수 있으므로 다음 처리를
항상 수행한다.

1. 편집 이미지를 `gemini-3.5-flash`로 다시 분석한다.
2. 새로 탐지된 `mask_supported=true` 영역을 모두 자동 마스킹한다.
3. 모든 얼굴에 FaceShield를 다시 적용한다.
4. EXIF 없는 PNG로 인코딩한다.
5. 결과 해시를 계산하고 최종 이미지만 업로드한다.

개인정보 재처리에 실패하면 생성 중간 이미지를 업로드하지 않고 요청 전체를 실패 처리한다.

### 성공 응답

```json
{
  "request_id": "req_01JZ456DEF",
  "status": "COMPLETED",
  "source_object_key": "protected/v2/a1b2c3d4/out_01JZ123ABC.png",
  "result_object_key": "protected/v2/a1b2c3d4/out_01JZ456DEF.png",
  "result_content_type": "image/png",
  "result_image_sha256": "764756dc1f3a740b27ce31ea8c94c763fb135cc5607dc2f347364ef29f75a820",
  "prompt_edit": {
    "applied": true
  },
  "privacy_postprocessing": {
    "masked_region_count": 0,
    "deepfake_protection_applied": true,
    "metadata_removed": true
  }
}
```

응답에는 사용자 프롬프트나 내부 `EditPlan`을 포함하지 않는다.

## 오류 코드

| HTTP | 코드 | 조건 |
| --- | --- | --- |
| 409 | `SOURCE_IMAGE_MISMATCH` | 기준 이미지 해시 불일치 |
| 409 | `IMAGE_ANALYSIS_MISMATCH` | Step 2 원본 해시 불일치 |
| 422 | `INVALID_PROMPT` | 프롬프트 형식 또는 길이 오류 |
| 422 | `INVALID_METADATA_POLICY` | v2 요청에서 메타데이터 제거가 비활성화됨 |
| 422 | `PROMPT_NOT_ALLOWED` | 개인정보 보호를 우회하는 편집 요청 |
| 502 | `IMAGE_EDIT_PROVIDER_ERROR` | 편집 모델이 유효한 이미지를 반환하지 않음 |
| 503 | `IMAGE_EDIT_MODEL_UNAVAILABLE` | 편집 모델 설정 또는 호출 불가 |
| 503 | `PRIVACY_POSTPROCESSING_FAILED` | 편집 후 개인정보 보호 재처리 실패 |
| 504 | `PROCESSING_TIMEOUT` | 처리 제한 시간 초과 |

그 밖의 저장소·이미지·FaceShield 오류는 v1 공통 오류 규칙을 따른다.

## Backend 작업 및 결과 버전

### `image_outputs`

`task_id`, `parent_output_id`, `version`, `operation_type`, `result_object_key`,
`result_image_sha256`, `prompt`, `processing_options`, `created_at`을 저장한다.

### `image_operations`

`task_id`, `output_id`, `operation_type`, `status`, `progress_stage`, 오류 정보와 생성·수정 시각을
저장한다. 상태는 `QUEUED`, `PROCESSING`, `SUCCESS`, `FAILURE`다.

Backend는 프롬프트를 애플리케이션 로그나 Celery 메시지 인자에 넣지 않는다. worker는 DB에서
프롬프트를 읽는다. 프롬프트 DB 보관 기간과 사용자 삭제 정책은 운영 전에 확정한다.
