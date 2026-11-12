"""delivery-eta API 계약 테스트

구조에서의 위치:
- 누가 부르나: `uv run pytest` (pyproject.toml 의 testpaths = api/tests).
- 무엇을 부르나: api/app.py 의 FastAPI 앱(`/health`, `/eta`)을 TestClient 로 호출한다.
  실제 서버를 띄우거나 네트워크를 쓰지 않는다.
- 무엇을 저장하나: 아무것도 저장하지 않는다.

원칙(AGENTS.md): 구현이 아니라 계약(응답 형식 · 상태 코드 · 알려진 입력의 예상값)을 확인한다.

범위: /eta 의 규칙 값과 응답 형식, 입력 검증(타입 · 음수 · 빠진 필드), /health.
범위 밖: 학습된 모델의 정확도(validate_model.py 담당), Go 서비스(parity_test.py 담당).

정답의 기준: 기대값은 사장님 규칙과 AGENTS.md 의 계약에서 계산한다. 현재 코드의 출력을 옮겨 적지 않는다.
- 사장님 규칙: 예상 시간(분) = 기본 10분 + 1km 당 3분 + 밀린 주문 1건당 2분.
- 응답 계약: EtaResponse.minutes 는 숫자(float) 이고, 잘못된 입력(숫자가 아님 · 음수 · 빠진 필드)은 422.
"""

import math

import pytest
from fastapi.testclient import TestClient

from api.app import MODEL_IMPL, app

client = TestClient(app)

# 사장님 규칙 값을 확인하는 테스트는 기준선 구현체(rule)일 때만 의미가 있다.
# (학습 모델의 값은 이 파일의 범위가 아니다 — validate_model.py 담당)
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
        (3.0, 2, 23.0),  # 사장님 규칙 예시: 10 + 3×3 + 2×2
        (2.0, 3, 22.0),  # 10 + 3*2 + 2*3
        (1.5, 0, 14.5),  # 소수 거리
        (0.0, 5, 20.0),  # 거리 0
    ],
)
def test_eta_known_values(distance_km, pending_orders, expected):
    """알려진 입력에 대해 사장님 규칙(10 + 3·거리 + 2·밀린 주문)의 값을 돌려주는지 확인한다."""
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
    """거리 상한(20km)과 아주 큰 주문 수에도 200 과 유한한 양수 시간을 돌려주는지 확인한다."""
    # 거리 20km 초과는 422 계약이라 큰 값 경계는 상한 거리 + 큰 주문 수로 본다.
    res = client.post("/eta", json={"distance_km": 20, "pending_orders": 100_000})

    assert res.status_code == 200
    minutes = res.json()["minutes"]
    assert math.isfinite(minutes)
    assert minutes > 0


@rule_only
def test_eta_large_inputs_value():
    """큰 입력에서도 사장님 규칙 값이 그대로 나오는지 확인한다."""
    res = client.post("/eta", json={"distance_km": 20, "pending_orders": 100_000})

    # approx 의 기본 상대오차(1e-6)는 값이 커지면 분 단위 차이도 넘겨 버리므로 정확히 비교한다.
    # (정수 입력이라 결과도 float 로 정확히 표현된다)
    assert res.json()["minutes"] == 10 + 3 * 20 + 2 * 100_000


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


# ---------- /eta 비정상 숫자 (NaN · 무한대 · 넘침) ----------
# 파이썬 json 은 표준 밖의 NaN · Infinity 도 읽으므로 본문을 문자열로 직접 보낸다.

JSON = {"Content-Type": "application/json"}


def test_eta_nan_returns_422():
    """NaN 은 숫자가 아니므로(AGENTS.md: 숫자가 아님 → 422) 422 를 돌려주는지 확인한다."""
    res = client.post("/eta", content='{"distance_km": NaN, "pending_orders": 0}', headers=JSON)

    assert res.status_code == 422


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param('{"distance_km": NaN, "pending_orders": 0}', id="distance-NaN"),
        pytest.param('{"distance_km": Infinity, "pending_orders": 0}', id="distance-Infinity"),
        pytest.param('{"distance_km": "inf", "pending_orders": 0}', id="distance-inf-string"),
        pytest.param('{"distance_km": 1e308, "pending_orders": 0}', id="distance-overflows-in-rule"),
        pytest.param('{"distance_km": 1, "pending_orders": 1' + "0" * 400 + "}", id="orders-10e400"),
    ],
)
def test_eta_extreme_numbers_keep_response_contract(raw):
    """비정상 숫자 입력에도 500 이나 숫자가 아닌 minutes(null 등)를 돌려주지 않는지 확인한다 (422 또는 유한한 minutes)."""
    res = client.post("/eta", content=raw, headers=JSON)

    # 계약: 거절하려면 422, 받아들였다면 minutes 는 유한한 숫자여야 한다.
    assert res.status_code in (200, 422), f"{res.status_code}: {res.text[:100]}"
    if res.status_code == 200:
        minutes = res.json()["minutes"]
        assert isinstance(minutes, (int, float)), f"minutes 가 숫자가 아님: {minutes!r}"
        assert math.isfinite(minutes)


# ---------- /eta 타입 엄격성 · 거리 상한 · 모르는 필드 ----------
# 아래 계약은 2026-11-12 사용자 지시로 정했다 (현재 코드 출력이 아니라 지시가 정답의 기준).
# - 따옴표 친 숫자("3")와 true/false 는 숫자가 아니므로 422.
# - 거리는 20km 까지만 받는다. 20km 초과는 422 (20km 자체는 허용).
# - 모르는 필드가 섞이면 422 (철자 틀린 필드가 조용히 무시되지 않게).

MAX_DISTANCE_KM = 20


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"distance_km": "3", "pending_orders": 0}, id="distance-quoted-number"),
        pytest.param({"distance_km": 1.0, "pending_orders": "2"}, id="orders-quoted-number"),
        pytest.param({"distance_km": True, "pending_orders": 0}, id="distance-true"),
        pytest.param({"distance_km": 1.0, "pending_orders": True}, id="orders-true"),
        pytest.param({"distance_km": 1.0, "pending_orders": False}, id="orders-false"),
    ],
)
def test_eta_quoted_number_or_bool_returns_422(payload):
    """따옴표 친 숫자와 true/false 는 숫자로 바꿔 받지 않고 422 를 돌려주는지 확인한다."""
    res = client.post("/eta", json=payload)

    assert res.status_code == 422, res.text[:100]


def test_eta_distance_at_limit_is_accepted():
    """거리 상한 20km 자체는 200 으로 받아들이는지 확인한다."""
    res = client.post("/eta", json={"distance_km": MAX_DISTANCE_KM, "pending_orders": 0})

    assert res.status_code == 200


@rule_only
def test_eta_distance_at_limit_value():
    """거리 20km 에서 사장님 규칙 값(10 + 3×20 = 70분)을 돌려주는지 확인한다."""
    res = client.post("/eta", json={"distance_km": MAX_DISTANCE_KM, "pending_orders": 0})

    assert res.json()["minutes"] == pytest.approx(70.0)


@pytest.mark.parametrize("distance_km", [20.01, 21, 500])
def test_eta_distance_over_limit_returns_422(distance_km):
    """20km 를 넘는 거리는 422 를 돌려주는지 확인한다."""
    res = client.post("/eta", json={"distance_km": distance_km, "pending_orders": 0})

    assert res.status_code == 422, res.text[:100]


def test_eta_unknown_field_returns_422():
    """계약에 없는 필드가 섞이면 무시하지 않고 422 를 돌려주는지 확인한다."""
    res = client.post("/eta", json={"distance_km": 1.0, "pending_orders": 0, "note": "빨리"})

    assert res.status_code == 422, res.text[:100]
