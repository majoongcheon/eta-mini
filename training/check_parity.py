"""예측 동등성(parity) 검사. Python API 와 다른 언어 서비스(Go, Rust)의 예측이 같은지 비교한다.

구조에서의 위치
  - 부르는 쪽: 사람이 직접(`python -m training.check_parity`) 또는 CI 단계.
    서버가 떠 있어야 하므로 pytest 테스트가 아니라 별도 스크립트다.
  - 부르는 대상: 각 서비스의 `POST /eta` (HTTP). 표준 라이브러리(urllib)만 쓴다.
  - 저장하는 것: 없음. 결과는 표준 출력으로만 보여 주고 종료코드로 알린다.

비교 방식
  - 기준: Python API (PY_URL). 학습한 원본 모델(MODEL_IMPL=sklearn)로 띄운 것이다.
  - 대상: TARGETS 환경 변수 ("이름=주소" 를 쉼표로 이어 쓴다).
  - 입력: (3, 2), (0, 0), (8, 10) + seed 0 으로 뽑은 무작위 20건 (거리 0.5~8km, 주문 0~10건).
  - 판정: 대상별 최대 오차가 TOLERANCE(1e-4분) 미만이면 PASS.
    하나라도 FAIL 이면 종료코드 1 (CI 가 여기서 멈춘다).

실행 예
  python -m training.check_parity
  PY_URL=http://127.0.0.1:8080 TARGETS=go=http://127.0.0.1:8081 python -m training.check_parity
"""
import json
import math
import os
import random
import sys
import urllib.error
import urllib.request

PY_URL = os.environ.get("PY_URL", "http://127.0.0.1:8080")
TARGETS = os.environ.get("TARGETS", "go=http://127.0.0.1:8081,rust=http://127.0.0.1:8082")
TOLERANCE = 1e-4   # 분. 이보다 작은 최대 오차만 같은 예측으로 본다
TIMEOUT = 5        # 초. 서버가 응답하지 않으면 실패로 처리한다


def make_inputs():
    """고정 3건 + seed 0 무작위 20건. 실행할 때마다 같은 입력이 나온다."""
    inputs = [(3.0, 2), (0.0, 0), (8.0, 10)]
    rng = random.Random(0)
    for _ in range(20):
        inputs.append((rng.uniform(0.5, 8.0), rng.randint(0, 10)))
    return inputs


def parse_targets(text):
    """'go=http://...,rust=http://...' → [('go', 'http://...'), ('rust', 'http://...')]"""
    result = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, url = item.partition("=")
        if not sep or not name.strip() or not url.strip():
            sys.exit(f"TARGETS 형식 오류: '{item}' (이름=주소 형식이어야 한다)")
        result.append((name.strip(), url.strip().rstrip("/")))
    if not result:
        sys.exit("TARGETS 가 비어 있다. 비교할 대상이 없다")
    return result


def predict(base_url, distance_km, pending_orders):
    """POST /eta 를 호출해 minutes 를 돌려준다. 실패하면 RuntimeError."""
    body = json.dumps({"distance_km": distance_km, "pending_orders": pending_orders}).encode()
    req = urllib.request.Request(
        base_url + "/eta", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            data = json.loads(res.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} (입력 {distance_km}km, {pending_orders}건)") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"연결 실패 {base_url}: {e}") from e
    except ValueError as e:
        raise RuntimeError(f"JSON 이 아닌 응답 {base_url}") from e

    minutes = data.get("minutes") if isinstance(data, dict) else None
    # bool 은 int 의 하위 형이라 따로 걸러 낸다. null · 문자열 · NaN · 무한대는 계약 위반이다.
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not math.isfinite(minutes):
        raise RuntimeError(f"minutes 가 유한한 숫자가 아님: {minutes!r} ({base_url})")
    return float(minutes)


def main():
    inputs = make_inputs()
    targets = parse_targets(TARGETS)

    # 기준 예측을 먼저 모두 받는다. 기준이 없으면 비교 자체가 안 되므로 바로 실패한다.
    try:
        base = [predict(PY_URL, d, n) for d, n in inputs]
    except RuntimeError as e:
        print(f"FAIL 기준 서버(PY_URL={PY_URL}) 호출 실패: {e}")
        return 1
    print(f"기준: {PY_URL}  입력 {len(inputs)}건")

    all_ok = True
    for name, url in targets:
        try:
            got = [predict(url, d, n) for d, n in inputs]
        except RuntimeError as e:
            print(f"FAIL [{name}] {e}")
            all_ok = False
            continue

        errors = [abs(a - b) for a, b in zip(base, got)]
        worst = max(range(len(errors)), key=errors.__getitem__)
        ok = errors[worst] < TOLERANCE
        all_ok = all_ok and ok
        d, n = inputs[worst]
        print(
            ("PASS " if ok else "FAIL ")
            + f"[{name}] 최대 오차 {errors[worst]:.3e}분 < {TOLERANCE:.0e}분"
            + f"  (최대 오차 입력: {d:.3f}km, {n}건 / 기준 {base[worst]:.6f} · 대상 {got[worst]:.6f})"
        )

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
