# delivery-eta API 이미지
# 구조에서의 위치: 프로젝트 루트를 컨텍스트로 빌드한다 (docker build -f api/Dockerfile -t eta-api .).
#   제외 목록은 api/Dockerfile.dockerignore (루트 컨텍스트라 api/.dockerignore 는 읽히지 않는다).
#   CI(.github/workflows/ci.yml)의 이미지 배포 단계도 같은 방식으로 빌드한다.
# 의존성은 uv.lock 에 고정된 버전으로 설치하고(--locked), 베이스 · uv 도 버전을 고정해
# 같은 커밋이면 같은 이미지가 나오게 한다. 올릴 때는 두 버전을 함께 바꾸고 테스트를 다시 돌린다.
FROM python:3.14.10-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache
ENV PATH="/app/.venv/bin:$PATH"

COPY api/ api/

ARG MODEL_IMPL=rule
ENV MODEL_IMPL=${MODEL_IMPL}

EXPOSE 8080
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8080"]
