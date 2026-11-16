# delivery-eta Rust 서비스 이미지 (ONNX 모델을 Rust(axum) + ONNX Runtime 으로 서빙한다)
# 구조에서의 위치: 저장소 루트를 컨텍스트로 빌드한다 (docker build -f api-rust/Dockerfile -t eta-rust .).
#   제외 목록은 api-rust/Dockerfile.dockerignore (루트 컨텍스트라 api-rust/.dockerignore 는 읽히지 않는다).
#   이미지에 넣는 모델 artifacts/model_sklearn.onnx 는 Git 에 없다 (.gitignore).
#   먼저 `uv run --group train python -m training.export_onnx` 로 만들어 두어야 빌드된다.
#
# ONNX Runtime 버전은 숫자를 적지 않고 빌드할 때 uv.lock 의 onnxruntime 버전을 읽어 쓴다.
#   Python API 와 같은 런타임으로 같은 모델을 돌려야 예측이 같다 (training/check_parity.py 로 확인).
# 파일은 TARGETARCH 에 맞춰 받는다 (amd64 → x64, arm64 → aarch64). BuildKit(docker build 기본)이 값을 채운다.
# ort 크레이트는 load-dynamic 이라 런타임을 내려받지 않고, 실행할 때 ORT_LIB 경로의 공유 라이브러리를 읽는다.
# 베이스 이미지는 버전을 고정해 같은 커밋이면 같은 이미지가 나오게 한다. 올릴 때는 테스트를 다시 돌린다.

# ---- 1) 빌드: Rust 서비스 컴파일 (Cargo.lock 대로만 받는다: --locked) ----
FROM rust:1.100.0-bookworm AS build
WORKDIR /src
# 의존성 컴파일 층을 캐시하려고, 소스를 넣기 전에 빈 main 으로 한 번 빌드한다.
COPY api-rust/Cargo.toml api-rust/Cargo.lock ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs && cargo build --release --locked && rm -rf src
COPY api-rust/src ./src
# 방금 넣은 소스가 캐시된 빈 빌드보다 새것으로 보이게 한다.
RUN touch src/main.rs && cargo test --release --locked && cargo build --release --locked

# ---- 2) ONNX Runtime 공유 라이브러리 받기 ----
FROM debian:bookworm-slim AS ort
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY uv.lock /tmp/uv.lock
RUN set -eu; \
    ver="$(awk '/^name = "onnxruntime"$/ { getline; gsub(/[^0-9.]/, "", $3); print $3; exit }' /tmp/uv.lock)"; \
    [ -n "$ver" ] || { echo "uv.lock 에서 onnxruntime 버전을 찾지 못했다" >&2; exit 1; }; \
    case "${TARGETARCH:-}" in \
      amd64) arch=x64 ;; \
      arm64) arch=aarch64 ;; \
      *) echo "지원하지 않는 TARGETARCH='${TARGETARCH:-}' (BuildKit 으로 빌드해야 한다)" >&2; exit 1 ;; \
    esac; \
    echo "ONNX Runtime ${ver} (${arch})"; \
    curl -fsSL -o /tmp/ort.tgz "https://github.com/microsoft/onnxruntime/releases/download/v${ver}/onnxruntime-linux-${arch}-${ver}.tgz"; \
    mkdir -p /out /tmp/ort; \
    tar -xzf /tmp/ort.tgz -C /tmp/ort --strip-components=1; \
    cp -L "/tmp/ort/lib/libonnxruntime.so.${ver}" /out/libonnxruntime.so

# ---- 3) 실행 이미지 ----
FROM debian:bookworm-slim
COPY --from=ort /out/libonnxruntime.so /usr/local/lib/libonnxruntime.so
COPY --from=build /src/target/release/eta-rust /usr/local/bin/eta-rust

WORKDIR /app
COPY artifacts/model_sklearn.onnx artifacts/model_sklearn.onnx

RUN useradd --system --no-create-home app
USER app

ENV PORT=8080 \
    MODEL_PATH=/app/artifacts/model_sklearn.onnx \
    ORT_LIB=/usr/local/lib/libonnxruntime.so

EXPOSE 8080
ENTRYPOINT ["eta-rust"]
