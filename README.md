# aiserver

FastAPI 기반 API 서버입니다. Python 및 의존성 관리는
[uv](https://docs.astral.sh/uv/)를 사용합니다.

## 개발 환경

```bash
uv sync --dev
cp .env.example .env
uv run fastapi dev app/main.py
```

서버 실행 후 `http://127.0.0.1:8000/docs`에서 API 문서를 확인할 수 있습니다.

## 검사

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

로깅 및 테스트 코드 작성 규칙은 각각
[`.agents/docs/logging.md`](.agents/docs/logging.md),
[`.agents/docs/testing.md`](.agents/docs/testing.md)를 참고합니다.
