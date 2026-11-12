"""규칙 구현체(rule) — 사장님 규칙으로 배달 예상 시간을 계산한다

구조에서의 위치:
- 누가 부르나: api/app.py (MODEL_IMPL=rule, 기본값), 학습 코드의 기준선 비교, api/tests/test_rule.py.
- 무엇을 부르나: 없음 (순수 계산).
- 무엇을 저장하나: 아무것도 저장하지 않는다.
다른 구현체(sklearn · dl · onnx)도 같은 함수 이름과 인자를 지킨다 (AGENTS.md 「API 계약」).
"""


def estimate_delivery_minutes(distance_km: float, pending_orders: int) -> float:
    """배달 예상 시간(분)을 계산

    모델 없이 배포하는 첫 구현체이자, 이후 모델들이 넘어야 할 기준선(baseline)이다.

    Args:
        distance_km: 가게에서 배달지까지 거리(km).
        pending_orders: 지금 밀려 있는 주문 수.

    Returns:
        예상 시간(분) = 기본 10분 + 1km당 3분 + 대기 주문 1건당 2분.
    """
    return 10.0 + 3.0 * distance_km + 2.0 * pending_orders
