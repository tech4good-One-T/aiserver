# AI 모델 및 처리 아키텍처

## 문서 목적

이 문서는 Tech4Good 이미지 보호 서비스의 무박 해커톤 MVP에서 실제로 사용하는
모델, 모델과 로컬 처리의 책임 경계, 개인정보 전송 경계와 알려진 한계를 고정한다.
API 계약은 다음 문서를 따른다.

- [이미지 API 공통 명세](api/common.md)
- [이미지 위험정보 분석 API](api/images-analyze.md)
- [이미지 보호 처리 API](api/images-process.md)

최우선 기준은 2025년까지 공개된 기술, 3~4시간 해커톤 구현 가능성, 구조화된
좌표 응답, 서버 준비 시간이다. 정확도와 개인정보의 완전한 자체 처리보다
해커톤 완주 가능성을 택한 구성임을 명확히 한다.

## 최종 구성

| 단계 | 선택 | 책임 | 실행 위치 |
| --- | --- | --- | --- |
| 시각 위험 탐지 | Gemini 2.5 Flash | 얼굴·신분증·번호판·문자·건물·위치 단서 분류, 구조화 좌표 생성 | Google Gemini API |
| EXIF·이미지 정규화 | Pillow | 파일 검증, EXIF 방향 보정, GPS 존재 여부, RGB PNG 정규화·해시 | AI 서버 CPU |
| 위험도 판정 | 서버 정책 테이블 | 탐지 타입별 `HIGH`/`MEDIUM`/`LOW`, 그룹 요약, 처리 액션 | AI 서버 CPU |
| 선택 영역 마스킹 | OpenCV | polygon 검증·병합 마스크와 Gaussian blur | AI 서버 CPU |
| 딥페이크 선제 방어 | FaceShield 공식 CLI | 얼굴 이미지에 보호 섭동 적용 | 같은 GPU 호스트의 별도 conda/CUDA 환경 |
| 원본·결과 전송 | HTTP Presigned URL | 허용 호스트 검증, 원본 GET, PNG PUT | AI 서버 CPU |

Qwen, PaddleOCR, MTCNN 등 별도 로컬 모델은 현재 MVP 런타임에서 사용하지 않는다.
Gemini가 시각 분류, 텍스트 인식, 좌표 생성과 얼굴 존재 판단을 모두 담당한다.

## Gemini 2.5 Flash 선택 이유

2025년 세대 멀티모달 모델은 OCR 결과만으로 알 수 없는 문서 종류, 화면 노출,
건물·여행 맥락과 같은 시각 의미를 하나의 요청에서 처리할 수 있다. Gemini 2.5 Flash는
이 특성에 구조화 출력을 결합할 수 있어, 여러 로컬 모델을 다운로드·서빙하는 데
드는 시간을 줄인다. Flash 계열은 해커톤의 지연 시간과 비용 제약에도 맞는다.

서버는 Gemini SDK의 response schema에 다음을 강제한다.

- 허용된 위험 그룹과 세부 타입 enum
- `confidence` 0~1
- 보이는 문자 또는 `null`
- `[ymin, xmin, ymax, xmax]` 순서의 0~1000 정규화 좌표

응답은 Pydantic으로 다시 검증하고, 타입과 그룹이 맞지 않거나 좌표가 잘못된
출력은 사용하지 않는다. 좌표는 서버가 EXIF 방향 보정 후 픽셀 좌표로 변환한다.

중요하게도 Gemini의 `confidence`는 시각적 탐지·분류 신뢰도이지 개인정보 위험도가
아니다. 세부 타입별 위험도는 `risk_policy.py`의 고정 정책이 결정하고, 한 그룹은
탐지 항목 중 가장 높은 위험도를 사용한다.

- 이미지 이해: <https://ai.google.dev/gemini-api/docs/image-understanding>
- 구조화 출력: <https://ai.google.dev/gemini-api/docs/structured-output>

## FaceShield 선택과 어댑터 경계

FaceShield는 ICCV 2025에 발표된 선제적 딥페이크 방어 기술이다. 위조물을 사후에
판별하는 모델이 아니라, 게시 전 이미지에 보호 섭동을 삽입해 얼굴 편집·교체
모델의 성공률을 낮추는 목적이다. 화풍 보호가 주목적인 Glaze·StyleGuard보다 서비스의
얼굴 딥페이크 위험에 직접 대응한다.

공식 구현은 API 서버의 Python 3.12 환경과 의존성이 다르므로 FastAPI에 import하지
않는다. 같은 GPU 호스트에 FaceShield 저장소와 별도 Python 3.8/CUDA conda 환경을 두고,
API가 요청 전용 임시 PNG를 생성한 뒤 `conda run ... bash execute.sh`를 shell 없이 실행한다.
공식 CLI의 10개 인자는 어댑터가 더하며 현재 기본값은 다음과 같다.

```text
resize_shape=512, proj_func=l1, attn_func=l2, attn_threshold=0.2,
arc_func=cosine, total_iter=30, noise_clamp=12, step_size=1
```

공식 구현은 안정적인 무얼굴 신호를 주지 않으므로 Gemini의 `FACE_EXPOSURE` 탐지가
CLI 실행 여부와 대상 영역을 결정한다. 서버는 각 얼굴 bbox에 `max(16px, 긴 변의
20%)` 여백을 더해 크롭한 뒤 크롭별로 FaceShield CLI를 호출한다. 보호된 크롭은 같은
위치에 합성해 전체 사진 해상도와 `/analyze` 좌표계를 유지한다. 공식 CLI가 512 근처로
크롭을 줄여 반환하면 `protected_small - clean_small`로 보호 delta를 계산한다. delta만
bilinear 보간으로 원래 크롭 크기에 매핑해 고해상도 clean crop에 더하며, 전체 사진과
원본 크롭 상세는 리사이즈하지 않는다. 가로세로 비율 차이가 2%를 넘는 FaceShield 출력은
안전하게 복원할 수 없으므로 실패로 처리한다.

얼굴이 있으면 사용자 옵션 없이 탐지된 모든 얼굴 크롭을 순차적으로 처리한다. 크롭 중
하나라도 실패하면 보호되지 않은 결과를 업로드하지 않고
`DEEPFAKE_PROTECTION_FAILED`를 반환한다.

어댑터는 임시 파일과 CLI 표준 출력을 로그에 남기지 않으며 성공·실패·취소와 관계없이
요청 임시 디렉터리를 제거한다. 현재 구현은 한 API 프로세스 안의 FaceShield 실행을
직렬화한다. 다만 여러 API worker를 실행하면 worker 간 GPU 동시 접근을 막지 못하므로
해커톤은 worker 1개로 실행한다.

- 공식 저장소: <https://github.com/kuai-lab/iccv25_faceshield>
- 논문: <https://openaccess.thecvf.com/content/ICCV2025/html/Jeong_FaceShield_Defending_Facial_Image_against_Deepfake_Threats_ICCV_2025_paper.html>

## 이미지 정규화와 SHA-256

두 API는 같은 함수로 입력을 정규화한다.

1. HTTP Content-Type과 Pillow가 감지한 JPEG·PNG·WebP 형식을 모두 검증한다.
2. 10MB와 4096×4096 제한을 검증한다.
3. 원본 EXIF에서 GPS 필드의 존재 여부만 로컬로 확인한다.
4. EXIF Orientation을 픽셀에 적용하고 RGB로 변환한다.
5. 메타데이터 없는 PNG(`compress_level=6`)로 인코딩한다.
6. Orientation을 1로 정규화한 EXIF와 키로 정렬한 Pillow `source.info` 항목을
   `image-metadata-v1` 형식의 canonical metadata fingerprint로 직렬화한다.
7. `SHA-256(normalized_png || 0x00 || metadata_fingerprint)`를 API `image.sha256`로 사용한다.

따라서 원본 JPEG/WebP 파일의 바이트 해시와 API 해시는 다르다. 픽셀이 같아도
해시 대상 EXIF나 `source.info` 메타데이터가 변경되면 API 해시가 달라진다. `/process`는
원본을 다시 다운로드해 동일한 정규화·지문을 생성한 뒤 `/analyze`가 반환한 해시와
비교한다.

## `/analyze` 처리 흐름

```text
Presigned GET 원본 다운로드
  -> 형식·크기 검증
  -> EXIF GPS 존재 여부를 로컬로 확인
  -> EXIF 방향 보정, RGB 변환, 정규화 PNG·metadata fingerprint·SHA-256 생성
  -> 크기 제한을 지킨 메타데이터 없는 PNG 또는 JPEG 전송본을 Gemini 2.5 Flash API에 전송
  -> 구조화 탐지 결과·정규화 좌표 검증
  -> EXIF GPS 탐지 항목 병합
  -> 로컬 정책으로 위험도·액션·픽셀 좌표 결정
  -> 일곱 위험 그룹과 detections 반환
```

Gemini에는 Presigned URL과 원본 EXIF를 전달하지 않는다. 정규화 PNG가 inline 크기 제한을
넘으면 종횡비를 유지한 JPEG 전송본으로 품질·크기를 낮추지만, 얼굴, 신분증, 번호판, 이름
등의 픽셀 개인정보는 모델 호출을 위해 Google에 전송된다. 전송본은 해시나 최종 결과에
사용하지 않는다.

## `/process` 처리 흐름

`/process`는 AI 서버에 저장된 `/analyze` 상태에 의존하지 않는다. 백엔드가 해시와
사용자가 선택한 polygon을 보관했다가 모두 요청에 넣는다. 얼굴 존재 여부도 저장된
분석 결과를 신뢰하지 않고 크기 제한을 지킨 메타데이터 없는 PNG 또는 JPEG 전송본을 Gemini에
다시 전송해 확인한다. 즉 정상적인
분석 후 처리에는 Gemini 호출이 총 2회 발생한다.

```text
Presigned GET 원본 다운로드
  -> 형식·크기 검증, EXIF 방향 보정, 정규화 PNG·metadata fingerprint·SHA-256 생성
  -> analysis_image_sha256 비교
  -> 크기 제한을 지킨 메타데이터 없는 PNG 또는 JPEG 전송본을 Gemini에 재전송해 FACE_EXPOSURE 존재 확인
  -> 사용자가 선택한 polygon만 OpenCV Gaussian blur
  -> 각 얼굴 bbox에 20%+ 여백을 더해 블러 결과에서 크롭
  -> 얼굴 크롭별 FaceShield CLI 필수 처리
  -> 크롭 크기가 변했으면 protected-clean delta만 bilinear로 매핑
  -> 고해상도 원본 크롭에 delta를 더해 원래 좌표에 합성
  -> remove_metadata에 맞게 손실 없는 PNG로 인코딩
  -> Presigned PUT으로 결과 업로드
  -> 메모리와 FaceShield 요청 임시 파일 폐기
```

얼굴이 없으면 `NO_FACE_DETECTED`로 건너뛴다. 얼굴이 있는데 Gemini 재호출 또는
FaceShield가 실패하면 필수 보호를 확인할 수 없으므로 결과를 업로드하지 않는다.

`remove_metadata=true`면 원본 EXIF를 제거한다. `false`면 회전 정보만 1로 정규화한 원본
EXIF를 PNG에 보존하므로 GPS 등 민감한 메타데이터도 남을 수 있다. 서비스 기본값은
`true`를 권장한다.

메타데이터 의미 분석·보존 범위는 EXIF뿐이다. XMP, IPTC, ICC 프로파일 등은 Pillow
`source.info`에 노출되면 변경 감지용 불투명 metadata fingerprint에는 포함되지만,
위험을 탐지·분석하거나 최종 PNG에 보존하지 않는다. 따라서 `remove_metadata=false`는 모든
원본 메타데이터 보존을 의미하지 않으며, XMP/IPTC에 노출된 위치정보는 현재 분석
결과에서 누락될 수 있다.

## 개인정보와 외부 전송

이 구성은 이미지 분석을 외부 API로 대체했으므로 **원본의 정규화된 픽셀이 Google
Gemini API에 제3자 제공된다.** 이를 자체 서버 내 처리 또는 외부 유출 불가 구성으로
설명해서는 안 된다.

해커톤 데모에서는 합성·테스트 이미지를 사용한다. 실제 사용자 이미지로 전환하려면
반드시 다음을 확인한다.

- 화면에 외부 AI 분석 전송을 명시하고 필요한 고지·동의를 받는다.
- 사용하는 Google 계정·결제 유형·지역의 현행 데이터 보관, 모델 개선 사용,
  삭제 및 하위 처리자 조건을 법무·보안 기준에 맞게 별도 검토한다. API 키를 쓰는
  것만으로 저장·학습 제외를 가정하지 않는다.
- API 키는 로컬 `.env` 또는 EC2 비밀 환경 파일에만 저장하고 Git, 채팅, 로그에
  남기지 않는다. 노출된 키는 폐기한다.
- background fallback으로 다른 외부 AI 제공자에 이미지를 재전송하지 않는다.

로컬 보호 원칙은 다음과 같다.

- AI 서버는 원본과 결과를 영구 저장하지 않는다.
- 이미지, 인식 문자, EXIF 값, Presigned URL, Gemini 원문 응답을 로그에 기록하지 않는다.
- EXIF GPS는 존재 여부만 로컬에서 확인하고 실제 위도·경도를 응답하거나 Gemini에
  전송하지 않는다.
- Presigned URL은 HTTPS, 정확한 허용 호스트·객체 경로를 검증하고 리다이렉트를 거부한다.
- S3 Block Public Access, 암호화, 짧은 Presigned URL 만료 시간을 사용한다.

외부 전송을 허용할 수 없는 서비스로 전환할 때는 Gemini를 로컬 VLM·OCR·얼굴
탐지 스택으로 교체해야 하며 현재 MVP 범위에는 포함하지 않는다.

## 이미지 변형 대응과 한계

서버는 FaceShield 이후 결과를 PNG로 손실 없이 저장해 서버 재압축으로 보호 섭동이
약화되는 것을 줄인다. 다만 FaceShield를 포함한 모든 선제 방어는 모든 후처리와
미래 생성 모델에 대한 완전한 차단을 보장하지 않는다.

특히 SNS 업로드 후의 JPEG 재압축, 강한 리사이즈, 얼굴 크롭, 화면 캡처·재촬영,
필터, 노이즈 제거, 여러 번의 재인코딩은 방어 효과를 약화할 수 있다. 이미지-투-동영상
모델에 대한 효과도 보장하지 않는다.

서버의 얼굴 크롭 전략은 전체 사진을 512로 줄이지 않고 주변 화질과 좌표를 유지하는
대응이다. 다만 작은 해상도에서 계산한 보호 delta를 bilinear로 키우는 과정이 섭동을
약화할 수 있고, Gemini bbox가 얼굴 전체와 필요한 맥락을 모두 포함하지 못할 수 있으므로
이 경로도 별도로 방어 효과를 검증해야 한다.

데모 이후에는 최소한 다음 변형을 적용한 보호 성공률과 화질을 함께 검증한다.

| 검증 | 최소 조건 |
| --- | --- |
| JPEG 재압축 | quality 95, 75, 50 |
| 리사이즈 | 원본의 75%, 50% |
| 크롭 | 외곽 5%, 10% |
| 화질 | 원본 대비 PSNR/SSIM과 육안 확인 |
| 실제 게시 경로 | 대상 SNS에 업로드·다운로드 후 재평가 |

발표에서는 "딥페이크를 완전 차단"이 아니라 "논문 기반의 선제 방어를 적용하며
생성 모델과 SNS 후처리 변화에 맞춰 계속 검증·업데이트가 필요하다"고 설명한다.

## 런타임과 해커톤 배포 기준

Gemini 분석은 외부 API이므로 FastAPI·Pillow·OpenCV 부분은 CPU로 실행된다. GPU와
대용량 가중치는 FaceShield에만 필요하다.

```text
GPU EC2 호스트 (Ubuntu 22.04 + NVIDIA driver/CUDA)
  ├─ FastAPI 프로세스: Python 3.12 + uv, worker 1
  ├─ FaceShield conda 환경: Python 3.8 + CUDA 의존성
  ├─ FaceShield 공식 저장소: 고정한 commit
  └─ 영구 모델 캐시: ArcFace/IP-Adapter/Stable Diffusion 관련 가중치
```

공식 저장소의 참고 환경은 Ubuntu 22.04, Python 3.8, CUDA 12.4, RTX A6000 48GB이다.
서로 다른 GPU에서의 VRAM·CUDA 호환성은 보장하지 않으므로 인스턴스를 고정하기 전에
샘플 1장으로 검증한다. 48GB GPU가 필요한 공식 환경에 가까운 AWS 대안으로는
`g6e.xlarge`(L40S 48GB)를 검토할 수 있지만, 사전 실행 확인 없이 호환성을 가정하지 않는다.

현재 `Dockerfile`은 Python 3.12 CPU API만 포함하고 FaceShield conda/CUDA 환경·저장소·가중치를
포함하지 않는다. 호스트 저장소와 conda도 컨테이너 안에 마운트되지 않으므로 현재
컨테이너만으로는 얼굴이 있는 `/process`를 완료할 수 없다. 해커톤의 전체 기능 기준은
API를 GPU EC2 호스트 Python 3.12 프로세스로 실행하고, 같은 호스트의 별도
FaceShield conda 환경을 CLI로 호출하는 방식이다.

최초 준비 시간을 줄이려면 발표 전에 다음을 완료한다.

1. 공식 FaceShield 저장소의 출처를 확인하고 검증한 commit SHA로 checkout한다.
2. 공식 안내의 ArcFace `models.zip`을 사전 다운로드·압축 해제하고 파일 무결성과
   경로를 확인한다.
3. Hugging Face에서 필요한 Stable Diffusion/IP-Adapter 가중치를 영구 EBS 캐시에 미리
   받고 사용 조건과 체크섬을 확인한다.
4. conda 환경 설치 후 샘플 1장을 CLI로 완주해 CUDA 호환성, 출력 경로, 캐시를
   예열한다.
5. `.env`의 `GEMINI_API_KEY`, 허용 S3 호스트, FaceShield 절대 경로를 설정하고
   해당 파일을 권한 600으로 보호한다.

현재 FaceShield 연결은 탐지된 얼굴 크롭마다 별도 CLI 프로세스를 시작하고 모델을
다시 로드한다. 다중 얼굴 이미지는 크롭 수만큼 순차 CLI 호출이 늘어난다.
따라서 지연 시간이 크고 동시 처리량이 낮으며, worker 1개와 단일 발표 데모를 위한
MVP 어댑터다. 서비스화할 때는 가중치를 한 번만 로드하는 전용 GPU worker·큐·백프레셔
구조로 교체해야 한다.

## 제외한 기술

- **Machine/face unlearning:** 외부 딥페이크 생성 모델의 가중치를 이 서비스가 제어할 수
  없으므로 게시 전 보호 흐름에 적용할 수 없다.
- **MetaCloak·StyleGuard·Glaze·Nightshade:** 무단 학습·화풍 모방 방지에 가까워 일반 사용자의
  얼굴 딥페이크 방어와 직접 맞지 않는다.
- **별도 로컬 VLM·OCR·얼굴 모델:** 외부 전송을 줄일 수 있지만 가중치 다운로드,
  GPU 메모리, 결과 병합과 오탐지 보정이 해커톤 범위를 크게 늘린다.

## 알려진 MVP 한계

- Gemini의 작은 문자, 반사, 모션 블러, 심한 원근 왜곡, 작은·측면·가려진 얼굴
  탐지는 누락·오탐지될 수 있다.
- 좌표와 `detected_text`는 구조화 스키마를 통과해도 픽셀 내용과 완전히 일치한다고
  보장할 수 없다.
- `/process`의 얼굴 존재·bbox 판단도 Gemini에 의존하므로 얼굴을 놓치거나 좌표가
  부정확하면 보호를 건너뛰거나 불충분한 크롭을 처리할 수 있다.
- FaceShield는 이미지-투-이미지 얼굴 편집 방어가 중심이며, 이미지-투-동영상과 미래의
  모든 딥페이크 모델에 대한 효과를 보장하지 않는다.
- 강한 보호 설정은 화질 저하와 처리 시간 증가로 이어질 수 있다.
- 현재 CLI 어댑터는 얼굴 크롭별 모델 로드로 인해 다중 얼굴·다중 사용자
  실시간 운영에 적합하지 않다.

이 한계 때문에 저신뢰 탐지 후보를 사용자가 확인하고 영역을 직접 추가·수정하는
기능, 전용 얼굴 탐지기와 상주 GPU worker는 후속 과제로 둔다.
