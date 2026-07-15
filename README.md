# aiserver

이미지에서 노출 위험을 탐지하고, 사용자가 선택한 영역을 블러한 뒤 얼굴이 있으면
FaceShield 보호를 적용하는 FastAPI 서버입니다.

- `POST /api/v1/images/analyze`: Gemini 3.5 Flash JSON 응답과 로컬 EXIF 검사로 위험정보·좌표 반환
- `POST /api/v1/images/process`: 선택 영역 OpenCV 블러, 얼굴 재확인, FaceShield 필수 적용,
  PNG 업로드

Python 및 의존성 관리는 [uv](https://docs.astral.sh/uv/)를 사용합니다.

## 개발 환경

```bash
uv sync --dev
cp .env.example .env
# .env의 ALLOWED_STORAGE_HOSTS, GEMINI_API_KEY를 본인 환경에 맞게 설정
uv run uvicorn app.main:app --env-file .env --reload
```

서버 실행 후 `http://127.0.0.1:8000/docs`에서 API 문서를 확인할 수 있습니다.
이미지는 백엔드가 발급한 HTTPS Presigned URL로 다운로드·업로드하며 AI 서버에
영구 저장하지 않습니다.

`GEMINI_API_KEY`는 `.env`에만 두고 커밋, 로그, 메신저에 남기지 않습니다. 이 구성은
이미지 픽셀을 Google Gemini API에 전송하므로 실제 개인정보를 처리하기 전에 반드시
이용자 고지·동의와 사용 계정의 데이터 처리 조건을 확인해야 합니다.

## FaceShield 실행 조건

`/analyze`는 Gemini API와 CPU 이미지 처리만으로 실행할 수 있지만, 얼굴이 있는 `/process`는
별도로 설치한 FaceShield CLI와 NVIDIA CUDA GPU가 필요합니다. API Python 3.12 프로세스가
`FACESHIELD_REPO_PATH`의 공식 저장소를 참조하고, `FACESHIELD_COMMAND`로 Python 3.8/CUDA conda
환경의 `execute.sh`를 호출합니다. 얼굴이 탐지됐는데 FaceShield가 설정되지 않았거나
실패하면 보호되지 않은 결과를 업로드하지 않고 `DEEPFAKE_PROTECTION_FAILED`를 반환합니다.

현재 [Dockerfile](Dockerfile)은 FastAPI CPU 런타임만 포함하며 FaceShield conda/CUDA·가중치를
포함하지 않습니다. 따라서 해커톤에서 전체 기능을 시연할 때는 GPU EC2 호스트에서
API를 Python 3.12 프로세스로 실행하고, 같은 호스트의 별도 FaceShield conda 환경을 CLI로
호출하는 구성을 사용합니다. 설치·배포 제약은
[AI 아키텍처](.agents/docs/ai_architect.md)와 [배포 문서](.agents/docs/deployment.md)를 참고합니다.

## 검사

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

로깅 및 테스트 코드 작성 규칙은 각각
[`.agents/docs/logging.md`](.agents/docs/logging.md),
[`.agents/docs/testing.md`](.agents/docs/testing.md)를 참고합니다.

## 컨테이너와 배포

```bash
docker build -t aiserver1:local .
docker run --rm --env-file .env -p 8001:8000 aiserver1:local
```

이 컨테이너는 `/health`, `/analyze`, 얼굴이 없는 `/process` 개발 검증에는 사용할 수
있지만, FaceShield가 필요한 요청은 위의 별도 GPU 런타임 없이 완료할 수 없습니다.

`main` 브랜치의 이미지는 GHCR에 게시한 뒤 GitHub OIDC와 AWS Systems Manager를 통해
서울 리전 EC2의 `8001` 포트로 배포합니다. 최초 AWS 및 EC2 준비 방법은
[`.agents/docs/deployment.md`](.agents/docs/deployment.md)를 참고합니다.
