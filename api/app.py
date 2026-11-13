"""delivery-eta API — 배달 예상 시간(ETA) 서비스

구조에서의 위치:
- 누가 부르나: uvicorn(`api.app:app`, api/Dockerfile 의 CMD), 테스트(api/tests/test_app.py 의 TestClient).
- 무엇을 부르나: 환경변수 MODEL_IMPL 로 고른 구현체 `api.predictors.<MODEL_IMPL>` 의
  estimate_delivery_minutes(distance_km, pending_orders). 기본은 규칙 구현체 rule.
- 무엇을 저장하나: 아무것도 저장하지 않는다.

계약(AGENTS.md 「API 계약」):
- POST /eta: distance_km(0~20km 숫자), pending_orders(0 이상 정수) → {"minutes": float, "model": str}
- 잘못된 입력(숫자가 아님 · 따옴표 친 숫자 · true/false · 음수 · NaN/무한대 · 20km 초과 · 빠진 필드 · 모르는 필드)은 422.
- GET /health: {"status": "ok", "model": MODEL_IMPL}
- GET /: API 소개 HTML 페이지 (JSON 계약 밖. 사람이 브라우저로 보는 안내문)
"""

import html
import importlib
import math
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

MODEL_IMPL = os.getenv("MODEL_IMPL", "rule")
estimate_delivery_minutes = importlib.import_module(f"api.predictors.{MODEL_IMPL}").estimate_delivery_minutes

MAX_DISTANCE_KM = 20  # 거리 상한(km). 2026-11-12 사용자 지시로 정한 계약.


class EtaRequest(BaseModel):
    """배달 예상 시간 요청"""

    # strict: "3" · true 같은 값을 숫자로 바꿔 받지 않는다.
    # extra=forbid: 모르는 필드(철자 틀린 필드 포함)를 조용히 무시하지 않고 422.
    # allow_inf_nan=False: NaN · 무한대를 422 로 거른다 (응답에 null 이 나가는 것을 막는다).
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)

    # 모델이 더 많은 정보를 원하면 여기에 필드를 추가
    distance_km: float = Field(ge=0, le=MAX_DISTANCE_KM, description="가게에서 배달지까지 거리(km)")
    pending_orders: int = Field(ge=0, description="지금 밀려 있는 주문 수")


class EtaResponse(BaseModel):
    """배달 예상 시간 응답"""

    minutes: float
    model: str  # 어떤 구현체가 낸 예측인지 (장애 추적용)


app = FastAPI(title="delivery-eta")


def _json_safe(value):
    """422 응답에 담을 입력값에서 JSON 으로 못 쓰는 NaN · 무한대를 문자열로 바꾼다."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)  # nan → "nan", inf → "inf"
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """입력 검증 실패를 422 로 돌려준다.

    기본 처리기는 오류 상세에 입력값(NaN 등)을 그대로 담아 JSON 변환에서 500 이 나므로,
    입력값을 JSON 으로 쓸 수 있게 바꾼 뒤 같은 형식({"detail": [...]})으로 돌려준다.
    """
    return JSONResponse(status_code=422, content={"detail": _json_safe(jsonable_encoder(exc.errors()))})


def _error_detail(msg: str) -> list[dict[str, object]]:
    """계산 단계의 422 도 입력 검증 오류와 같은 모양({"detail": [{"loc", "msg", "type"}]})으로 맞춘다."""
    return [{"loc": ["body"], "msg": msg, "type": "value_error"}]


# "/" 소개 페이지. 외부 CSS · JS · 이미지를 쓰지 않는 한 장짜리 HTML 이다.
# 구현체 이름만 요청 때마다 채운다 (str.format 이 아니라 replace 를 써서 CSS 의 중괄호와 겹치지 않게 한다).
_LANDING_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>delivery-eta API</title>
<style>
  body { font-family: system-ui, "Malgun Gothic", sans-serif; max-width: 42rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; color: #222; }
  h1 { margin-bottom: 0; }
  .sub { color: #666; margin-top: 0.25rem; }
  pre { background: #f4f4f4; padding: 0.75rem 1rem; overflow-x: auto; border-radius: 6px; }
  code { background: #f4f4f4; padding: 0.1rem 0.3rem; border-radius: 4px; }
  pre code { padding: 0; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #ddd; padding: 0.4rem 0.6rem; text-align: left; }
</style>
</head>
<body>
<h1>delivery-eta API</h1>
<p class="sub">배달 예상 시간(ETA)을 분 단위로 알려 주는 API · 현재 구현체: <code>__MODEL__</code></p>

<h2>엔드포인트</h2>
<table>
<tr><th>메서드</th><th>주소</th><th>설명</th></tr>
<tr><td>POST</td><td><code>/eta</code></td><td>거리와 밀린 주문 수로 예상 시간(분)을 계산한다</td></tr>
<tr><td>GET</td><td><code>/health</code></td><td>서비스 상태와 구현체 이름을 돌려준다</td></tr>
<tr><td>GET</td><td><a href="/docs"><code>/docs</code></a></td><td>자동 생성 API 문서 (직접 호출해 볼 수 있다)</td></tr>
</table>

<h2>사용 예</h2>
<pre><code>curl -X POST ./eta   -H "Content-Type: application/json"   -d '{"distance_km": 3, "pending_orders": 2}'

→ {"minutes": 23.0, "model": "__MODEL__"}</code></pre>

<h2>입력 규칙</h2>
<ul>
<li><code>distance_km</code>: 0 이상 20 이하의 숫자 (km)</li>
<li><code>pending_orders</code>: 0 이상의 정수 (밀린 주문 수)</li>
<li>두 필드만 받는다. 숫자를 따옴표로 감싸거나, true/false · 음수 · 20km 초과 · 빠진 필드 · 모르는 필드는 <b>422</b> 로 거절한다.</li>
</ul>

<h2>계산 규칙 (rule 구현체)</h2>
<p>예상 시간(분) = 기본 10분 + 1km 당 3분 + 밀린 주문 1건당 2분</p>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    """API 소개 페이지(HTML)를 돌려준다."""
    return HTMLResponse(_LANDING_HTML.replace("__MODEL__", html.escape(MODEL_IMPL)))


@app.get("/health")
def health() -> dict[str, str]:
    """서비스 상태와 현재 구현체 이름을 돌려준다."""
    return {"status": "ok", "model": MODEL_IMPL}


@app.post("/eta", response_model=EtaResponse)
def eta(req: EtaRequest) -> EtaResponse:
    """배달 예상 시간을 계산"""
    try:
        minutes = estimate_delivery_minutes(req.distance_km, req.pending_orders)
    except OverflowError:
        # 주문 수가 너무 커서(예: 10**400) 계산이 넘치면 500 대신 422 로 거절한다.
        raise HTTPException(status_code=422, detail=_error_detail("입력이 너무 커서 계산할 수 없습니다"))
    if not math.isfinite(minutes):
        # 응답 계약(minutes: 유한한 숫자)을 지킬 수 없으면 null 을 내보내지 않고 거절한다.
        raise HTTPException(status_code=422, detail=_error_detail("계산 결과가 유효한 숫자가 아닙니다"))
    return EtaResponse(minutes=minutes, model=MODEL_IMPL)
