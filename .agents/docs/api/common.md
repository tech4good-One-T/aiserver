# 이미지 API 공통 명세

## 가정

- AI 서버는 원본 이미지와 처리 결과 이미지를 영구 저장하지 않는다.
- `/process`는 AI 서버에 저장된 `/analyze` 상태에 의존하지 않고 독립적으로 실행한다.
- `/process` 호출자는 이미지 해시와 선택 좌표 등 처리에 필요한 분석 정보를 요청에 모두
  포함해야 한다.
- `/process`는 저장된 얼굴 탐지 상태도 사용하지 않으며, FaceShield 실행 여부를
  결정하기 위해 입력 이미지를 Gemini API에 다시 전송한다.
- 모든 좌표는 EXIF 회전 보정 후 이미지 기준 픽셀 좌표다.
- 원본 다운로드와 결과 업로드에는 백엔드가 발급한 짧은 만료 시간의 Presigned URL을 사용한다.
- 백엔드는 만료되는 Presigned URL이 아니라 `source_object_key`와 `result_object_key`를 영구
  저장한다.

## 공통 규칙

```text
Base URL: /api/v1
지원 형식: image/jpeg, image/png, image/webp (정적 이미지에 한함)
최대 크기: 10MB
최대 해상도: 4096×4096
좌표계: EXIF 회전 보정 후 이미지 기준 픽셀 좌표
`/process` 선택 영역: 최대 100개
한 polygon의 좌표 점: 3~128개
```

이미지의 실제 Content-Type과 파일 시그니처가 일치해야 한다. 확장자나 HTTP Content-Type만으로
이미지 형식을 신뢰하지 않는다. GIF는 지원하지 않으며, PNG/APNG·WebP의 애니메이션 입력도
첫 프레임만 처리하지 않고 `UNSUPPORTED_IMAGE_FORMAT`으로 거부한다.

## 정규화 이미지와 해시

`image.sha256`와 `analysis_image_sha256`은 원본 파일 바이트의 해시가 아니다. 서버가
생성한 **정규화 PNG 바이트와 canonical metadata fingerprint를 결합한 값의
SHA-256**다.

```text
SHA-256(normalized_png || 0x00 || metadata_fingerprint)
```

- `normalized_png`: EXIF Orientation을 적용하고 RGB로 변환한 뒤, 메타데이터 없는
  PNG를 Pillow `compress_level=6`으로 인코딩한 바이트
- `metadata_fingerprint`: `image-metadata-v1` 버전 표식, Orientation을 1로 정규화한
  EXIF 바이트, `exif` 중복 항목을 제외하고 키로 정렬한 Pillow `source.info` 항목을
  길이와 함께 결정적으로 직렬화한 바이트

이 지문 때문에 픽셀이 같아도 EXIF나 Pillow가 `source.info`로 노출한 메타데이터가
변경되면 `/process`에서 `IMAGE_ANALYSIS_MISMATCH`가 발생한다.

`/analyze`와 `/process`는 동일한 정규화 로직을 사용한다. 백엔드는 해시를 재계산해
변환하지 말고 `/analyze` 응답의 64자리 소문자 16진수 값을 그대로 보관해
`/process` 요청에 전달한다.

## 메타데이터 MVP 범위

현재 구현이 의미를 해석하고 선택적으로 보존하는 메타데이터는 EXIF뿐이다. XMP,
IPTC, ICC 프로파일과 기타 전용 메타데이터는 Pillow `source.info`에 노출되면 변경
감지용 불투명 fingerprint에는 포함되지만, 위험을 탐지·분석하거나 최종 PNG에 보존하지
않는다. 따라서 `/process` 요청의 `remove_metadata=false`는 “모든 원본 메타데이터 보존”이
아니라 “회전 값을 1로 정규화한 EXIF만 보존”을 뜻한다. `true`면 EXIF도 복사하지 않는다.

위치 위험 탐지는 EXIF GPS의 존재 여부만 확인한다. XMP/IPTC 등에 위치·식별
정보가 있어도 MVP는 별도 위험 항목으로 탐지하지 못한다.

## 외부 AI 처리 경계

정규화 PNG는 해시와 최종 이미지 기준을 위한 canonical 표현이다. Gemini에는 EXIF가 없는
정규화 PNG를 우선 사용하고, inline 크기 제한을 넘을 때만 메타데이터 없는 JPEG로
품질을 낮추거나 최대 80%씩 축소한 전송본을 보낸다. 이 전송본은 해시·출력 파일로
사용하지 않으며 종횡비를 유지하므로 0~1000 좌표 매핑은 동일하다. GPS는 AI 서버가 로컬에서
존재 여부만 검사한다. 하지만 얼굴·신분증·번호판·문자 등 이미지에 보이는 픽셀 정보는
Gemini API에 전송된다. 따라서 이 구성을 외부 전송 없는 구성으로
설명하지 않으며, 실제 개인정보 처리 전에 이용자 고지·동의와 현행 제공자
데이터 처리 조건을 별도로 검토한다.

## Presigned URL 규칙

- 다운로드 URL은 GET, 업로드 URL은 PUT 권한만 허용한다.
- URL은 HTTPS만 허용하고 AI 서버에 설정된 Object Storage 호스트와 버킷만 허용한다.
- URL 리다이렉트를 허용하지 않는다.
- 권장 만료 시간은 10~15분이다.
- Presigned URL과 쿼리 문자열은 로그 또는 데이터베이스에 기록하지 않는다.
- AI 서버에 AWS Access Key나 버킷 쓰기 권한을 직접 제공하지 않는다.

## 요청 추적

모든 응답은 `request_id`를 포함한다. 성공 응답은 JSON 본문에, 오류 응답은
`error.request_id`에 같은 값을 사용한다. 로그에도 같은 필드명 `request_id`를 사용하되 이미지,
OCR 원문, EXIF 값과 Presigned URL은 기록하지 않는다.

## 공통 오류 응답

```http
Content-Type: application/json
```

```json
{
  "error": {
    "code": "UNSUPPORTED_IMAGE_FORMAT",
    "message": "지원하지 않는 이미지 형식입니다.",
    "request_id": "req_01J..."
  }
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `error.code` | String | 클라이언트가 분기 처리할 안정적인 오류 코드 |
| `error.message` | String | 사용자 또는 개발자가 확인할 수 있는 오류 설명 |
| `error.request_id` | String | 요청과 서버 로그를 연결하는 추적 ID |

엔드포인트별 HTTP 상태와 오류 코드는 각 API 문서에서 정의한다.

## 관련 API

- [이미지 위험정보 분석 API](images-analyze.md)
- [이미지 보호 처리 API](images-process.md)
- [v2 이미지 후처리 및 프롬프트 편집 API](images-v2-process-edit.md)
