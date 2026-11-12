"""규칙 구현체(rule) 단위 테스트

구조에서의 위치:
- 누가 부르나: `uv run pytest` (pyproject.toml 의 testpaths = api/tests).
- 무엇을 부르나: api/predictors/rule.py 의 estimate_delivery_minutes 를 직접 호출한다.
  API(app.py) · 입력 검증은 거치지 않는다 — 그쪽은 test_app.py 담당.
- 무엇을 저장하나: 아무것도 저장하지 않는다.

정답의 기준: 사장님 규칙 "예상 시간(분) = 기본 10분 + 1km 당 3분 + 밀린 주문 1건당 2분".
기대값은 이 규칙에서 손으로 계산한 값이며, 현재 코드의 출력을 옮겨 적지 않는다.
범위 밖: 음수 · NaN · 무한대 · 타입 같은 입력 검증(API 의 EtaRequest 가 담당, test_app.py).
"""

import pytest

from api.predictors.rule import estimate_delivery_minutes

BASE_MIN = 10  # 기본 10분
PER_KM_MIN = 3  # 1km 당 3분
PER_ORDER_MIN = 2  # 밀린 주문 1건당 2분


@pytest.mark.parametrize(
    ("distance_km", "pending_orders", "expected"),
    [
        (0, 0, 10.0),  # 기본만
        (3, 2, 23.0),  # 사장님 규칙 예시: 10 + 3×3 + 2×2
        (1, 0, 13.0),  # 10 + 3
        (0, 1, 12.0),  # 10 + 2
        (2.5, 4, 25.5),  # 10 + 7.5 + 8
        (20, 0, 70.0),  # API 거리 상한: 10 + 60
        (0.1, 0, 10.3),  # 소수 거리
    ],
)
def test_rule_known_values(distance_km, pending_orders, expected):
    """알려진 입력에 대해 사장님 규칙으로 손계산한 값을 돌려주는지 확인한다."""
    assert estimate_delivery_minutes(distance_km, pending_orders) == pytest.approx(expected)


def test_rule_base_minutes():
    """거리 0 · 주문 0 이면 기본 10분만 돌려주는지 확인한다."""
    assert estimate_delivery_minutes(0, 0) == pytest.approx(BASE_MIN)


@pytest.mark.parametrize("distance_km", [0, 1, 7.5, 19])
def test_rule_each_km_adds_3_minutes(distance_km):
    """거리 1km 가 늘 때마다 정확히 3분이 늘어나는지 확인한다 (주문 수 고정)."""
    before = estimate_delivery_minutes(distance_km, 2)
    after = estimate_delivery_minutes(distance_km + 1, 2)

    assert after - before == pytest.approx(PER_KM_MIN)


@pytest.mark.parametrize("pending_orders", [0, 1, 10, 99])
def test_rule_each_order_adds_2_minutes(pending_orders):
    """밀린 주문 1건이 늘 때마다 정확히 2분이 늘어나는지 확인한다 (거리 고정)."""
    before = estimate_delivery_minutes(3.0, pending_orders)
    after = estimate_delivery_minutes(3.0, pending_orders + 1)

    assert after - before == pytest.approx(PER_ORDER_MIN)


def test_rule_large_orders_exact():
    """주문 수가 아주 커도 규칙 값이 정확히 나오는지 확인한다 (approx 대신 == 로 비교)."""
    # approx 의 상대오차는 큰 값에서 넓어져 1분 차이를 놓치므로 정확히 비교한다.
    assert estimate_delivery_minutes(20, 100_000) == 10 + 3 * 20 + 2 * 100_000


def test_rule_returns_float_for_int_inputs():
    """정수를 넣어도 float 를 돌려주는지 확인한다 (응답 계약 minutes: float)."""
    result = estimate_delivery_minutes(1, 1)

    assert isinstance(result, float)


def test_rule_is_deterministic():
    """같은 입력이면 매번 같은 값을 돌려주는지 확인한다 (기준선은 고정돼야 한다)."""
    results = {estimate_delivery_minutes(4.2, 3) for _ in range(5)}

    assert len(results) == 1
