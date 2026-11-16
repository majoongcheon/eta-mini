# delivery-eta Go 서비스 이미지 (ONNX 모델을 Go + ONNX Runtime 으로 서빙한다)
# 구조에서의 위치: 저장소 루트를 컨텍스트로 빌드한다 (docker build -f api-go/Dockerfile -t eta-go .).
#   제외 목록은 api-go/Dockerfile.dockerignore (루트 컨텍스트라 api-go/.dockerignore 는 읽히지 않는다).
#   이미지에 넣는 모델 artifacts/model_sklearn.onnx 는 Git 에 없다 (.gitignore).
#   먼저 `uv run --group train python -m training.export_onnx` 로 만들어 두어야 빌드된다.
#
# ONNX Runtime 버전은 숫자를 적지 않고 빌드할 때 uv.lock 의 onnxruntime 버전을 읽어 쓴다.
#   Python API 와 같은 런타임으로 같은 모델을 돌려야 예측이 같다 (training/check_parity.py 로 확인).
# 파일은 TARGETARCH 에 맞춰 받는다 (amd64 → x64, arm64 → aarch64). BuildKit(docker build 기본)이 값을 채운다.
# 베이스 이미지는 버전을 고정해 같은 커밋이면 같은 이미지가 나오게 한다. 올릴 때는 테스트를 다시 돌린다.

# ---- 1) 빌드: Go 서비스 컴파일 (onnxruntime_go 가 cgo 라 CGO_ENABLED=1) ----
FROM golang:1.27.4-bookworm AS build
WORKDIR /src
COPY api-go/go.mod api-go/go.sum ./
RUN go mod download
COPY api-go/ ./
RUN go test ./... && CGO_ENABLED=1 go build -trimpath -ldflags="-s -w" -o /out/eta-go .

# ---- 2) ONNX Runtime 공유 라이브러리 받기 ----
FROM golang:1.27.4-bookworm AS ort
ARG TARGETARCH
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
COPY --from=build /out/eta-go /usr/local/bin/eta-go

WORKDIR /app
COPY artifacts/model_sklearn.onnx artifacts/model_sklearn.onnx

RUN useradd --system --no-create-home app
USER app

ENV PORT=8080 \
    MODEL_PATH=/app/artifacts/model_sklearn.onnx \
    ONNXRUNTIME_LIB=/usr/local/lib/libonnxruntime.so

EXPOSE 8080
ENTRYPOINT ["eta-go"]
