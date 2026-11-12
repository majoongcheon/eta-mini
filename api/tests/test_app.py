"""delivery-eta API 계약 테스트

구조에서의 위치:
- 누가 부르나: `uv run pytest` (pyproject.toml 의 testpaths = api/tests).
- 무엇을 부르나: api/app.py 의 FastAPI 앱(`/health`, `/eta`)을 TestClient 로 호출한다.
  실제 서버를 띄우거나 네트워크를 쓰지 않는다.
- 무엇을 저장하나: 아무것도 저장하지 않는다.

원칙(AGENTS.md): 구현이 아니라 계약(응답 형식 · 상태 코드 · 알려진 입력의 예상값)을 확인한다.
"""

import math

import pytest
from fastapi.testclient import TestClient

from api.app import MODEL_IMPL, app

client = TestClient(app)

# 예상값을 고정해 확인하는 테스트는 기준선 구현체(rule)일 때만 의미가 있다.
# MODEL_IMPL 을 다른 구현체로 바꿔 돌리면 이 테스트들은 건너뛴다.
rule_only = pytest.mark.skipif(MODEL_IMPL != "rule", reason="예상값은 rule 구현체 기준")


# ---------- /health ----------


def test_health_returns_ok_and_model_name():
    """/health 가 200 과 status=ok, 현재 구현체 이름을 돌려주는지 확인한다."""
    res = client.get("/health")

    assert res.status_code == 200
    assert res.json() == {"status": "ok", "model": MODEL_IMPL}


# ---------- /eta 정상 입력 · 응답 형식 ----------


def test_eta_response_shape():
    """/eta 응답이 minutes(숫자) 와 model(문자열) 두 필드만 갖는지 확인한다."""
    res = client.post("/eta", json={"distance_km": 2.5, "pending_orders": 3})

    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"minutes", "model"}
    assert isinstance(body["minutes"], (int, float))
    assert body["model"] == MODEL_IMPL


@rule_only
@pytest.mark.parametrize(
    ("distance_km", "pending_orders", "expected"),
    [
        (2.0, 3, 22.0),  # 10 + 3*2 + 2*3
        (1.5, 0, 14.5),  # 소수 거리
        (0.0, 5, 20.0),  # 거리 0
    ],
)
def test_eta_known_values(distance_km, pending_orders, expected):
    """알려진 입력에 대해 기준선 공식(10 + 3·거리 + 2·대기주문)의 예상값을 돌려주는지 확인한다."""
    res = client.post("/eta", json={"distance_km": distance_km, "pending_orders": pending_orders})

    assert res.status_code == 200
    assert res.json()["minutes"] == pytest.approx(expected)


# ---------- /eta 경계값 ----------


@rule_only
def test_eta_zero_inputs():
    """거리 0, 대기 주문 0 (허용 하한) 이 200 과 기본 10분을 돌려주는지 확인한다."""
    res = client.post("/eta", json={"distance_km": 0, "pending_orders": 0})

    assert res.status_code == 200
    assert res.json()["minutes"] == pytest.approx(10.0)


def test_eta_large_inputs():
    """아주 큰 거리 · 주문 수도 200 과 유한한 양수 시간을 돌려주는지 확인한다."""
    res = client.post("/eta", json={"distance_km": 1_000_000, "pending_orders": 100_000})

    assert res.status_code == 200
    minutes = res.json()["minutes"]
    assert math.isfinite(minutes)
    assert minutes > 0


@rule_only
def test_eta_large_inputs_value():
    """큰 입력에서도 기준선 공식 값이 그대로 나오는지 확인한다."""
    res = client.post("/eta", json={"distance_km": 1_000_000, "pending_orders": 100_000})

    # approx 의 기본 상대오차(1e-6)는 값이 커지면 수 분 차이도 넘겨 버리므로 정확히 비교한다.
    # (정수 입력이라 결과도 float 로 정확히 표현된다)
    assert res.json()["minutes"] == 10 + 3 * 1_000_000 + 2 * 100_000


# ---------- /eta 잘못된 입력 → 422 ----------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"distance_km": -0.1, "pending_orders": 0}, id="distance-negative"),
        pytest.param({"distance_km": 1.0, "pending_orders": -1}, id="orders-negative"),
        pytest.param({"distance_km": "abc", "pending_orders": 0}, id="distance-not-number"),
        pytest.param({"distance_km": 1.0, "pending_orders": "many"}, id="orders-not-number"),
        pytest.param({"distance_km": 1.0, "pending_orders": 1.5}, id="orders-fractional"),
        pytest.param({"distance_km": None, "pending_orders": 0}, id="distance-null"),
        pytest.param({"pending_orders": 0}, id="distance-missing"),
        pytest.param({"distance_km": 1.0}, id="orders-missing"),
        pytest.param({}, id="empty-body"),
    ],
)
def test_eta_invalid_input_returns_422(payload):
    """음수 · 숫자가 아닌 값 · 빠진 필드는 422 와 오류 상세(detail)를 돌려주는지 확인한다."""
    res = client.post("/eta", json=payload)

    assert res.status_code == 422
    assert "detail" in res.json()


def test_eta_non_json_body_returns_422():
    """JSON 이 아닌 본문은 422 를 돌려주는지 확인한다."""
    res = client.post("/eta", content="not json", headers={"Content-Type": "application/json"})

    assert res.status_code == 422
