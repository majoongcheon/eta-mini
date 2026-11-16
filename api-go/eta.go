// 입력 검증 + HTTP 핸들러. api/app.py 와 같은 API 계약(AGENTS.md 「API 계약」)을 지킨다.
//
// 구조에서의 위치
//   - 누가 부르나: main.go 가 라우터에 연결한다. 테스트(eta_test.go)는 가짜 Predictor 로 직접 부른다.
//   - 무엇을 부르나: Predictor.Predict (실제로는 predictor.go 의 ONNX 구현).
//   - 무엇을 저장하나: 아무것도 저장하지 않는다.
//
// 계약 요약
//   - POST /eta: {"distance_km": 0~20 숫자, "pending_orders": 0 이상 정수} 두 필드만.
//     숫자가 아님 · 따옴표 친 숫자 · true/false · 소수인 주문 수 · 음수 · NaN/무한대 · 20km 초과 ·
//     빠진 필드 · 모르는 필드 · 계산이 넘치는 큰 값은 422. 500 이나 minutes: null 은 계약 위반.
//   - GET /health: {"status": "ok", "model": 구현체 이름}
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"math"
	"mime"
	"net/http"
	"regexp"
	"strconv"
	"strings"
)

// 거리 상한(km). api/app.py 의 MAX_DISTANCE_KM 과 같은 값이다 (2026-11-12 사용자 지시로 정한 계약).
const maxDistanceKm = 20

// 요청 본문 크기 상한. 두 숫자만 받으므로 1 MiB 면 충분하다.
const maxBodyBytes = 1 << 20

// Predictor 는 배달 예상 시간(분)을 계산한다. distance_km 와 pending_orders 는 검증을 마친 값이다.
type Predictor interface {
	Predict(distanceKm, pendingOrders float64) (float64, error)
}

// 정수 리터럴(JSON 숫자 중 소수점 · 지수가 없는 것). "2.0" · "2e0" 은 정수로 보지 않는다 (pydantic strict 와 같다).
var intLiteral = regexp.MustCompile(`^-?(0|[1-9][0-9]*)$`)

// 검증 실패 메시지. 422 응답의 msg 로 그대로 나간다.
var errTooBig = errors.New("입력이 너무 커서 계산할 수 없습니다")

// parseEtaRequest 는 요청 본문을 검증해 (거리, 주문 수)를 돌려준다. 계약 위반이면 error.
func parseEtaRequest(body []byte) (distanceKm, pendingOrders float64, err error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(body, &fields); err != nil || fields == nil {
		return 0, 0, errors.New("요청 본문은 JSON 객체여야 합니다")
	}
	// 모르는 필드(철자 틀린 필드 포함)는 조용히 무시하지 않고 거절한다.
	for name := range fields {
		if name != "distance_km" && name != "pending_orders" {
			return 0, 0, errors.New("모르는 필드: " + name)
		}
	}
	rawDistance, ok := fields["distance_km"]
	if !ok {
		return 0, 0, errors.New("distance_km 가 없습니다")
	}
	rawOrders, ok := fields["pending_orders"]
	if !ok {
		return 0, 0, errors.New("pending_orders 가 없습니다")
	}

	distanceKm, err = parseNumber(rawDistance, "distance_km")
	if err != nil {
		return 0, 0, err
	}
	if distanceKm < 0 || distanceKm > maxDistanceKm {
		return 0, 0, errors.New("distance_km 는 0 이상 20 이하여야 합니다")
	}

	if !intLiteral.Match(rawOrders) {
		return 0, 0, errors.New("pending_orders 는 정수여야 합니다")
	}
	pendingOrders, err = strconv.ParseFloat(string(rawOrders), 64)
	if err != nil && !isRangeError(err) {
		return 0, 0, errors.New("pending_orders 는 정수여야 합니다")
	}
	if pendingOrders < 0 {
		return 0, 0, errors.New("pending_orders 는 0 이상이어야 합니다")
	}
	if math.IsInf(pendingOrders, 0) {
		return 0, 0, errTooBig // 10**400 처럼 실수로도 못 나타내는 값
	}
	return distanceKm, pendingOrders, nil
}

// parseNumber 는 JSON 숫자만 받아들인다. 문자열("3") · true/false · null 은 거절한다.
func parseNumber(raw json.RawMessage, name string) (float64, error) {
	// 본문 전체가 유효한 JSON 이므로, 숫자나 '-' 로 시작하면 JSON 숫자다.
	if len(raw) == 0 || !(raw[0] == '-' || (raw[0] >= '0' && raw[0] <= '9')) {
		return 0, errors.New(name + " 는 숫자여야 합니다")
	}
	v, err := strconv.ParseFloat(string(raw), 64)
	if err != nil && !isRangeError(err) {
		return 0, errors.New(name + " 는 숫자여야 합니다")
	}
	if math.IsInf(v, 0) || math.IsNaN(v) {
		return 0, errors.New(name + " 는 유한한 숫자여야 합니다") // 1e999 처럼 넘치는 값
	}
	return v, nil
}

func isRangeError(err error) bool {
	var ne *strconv.NumError
	return errors.As(err, &ne) && errors.Is(ne.Err, strconv.ErrRange)
}

// writeJSON 은 JSON 응답을 쓴다.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// writeUnprocessable 은 422 를 돌려준다. 모양은 FastAPI 와 같이 {"detail": [{"loc", "msg", "type"}]} 이다.
func writeUnprocessable(w http.ResponseWriter, msg string) {
	writeJSON(w, http.StatusUnprocessableEntity, map[string]any{
		"detail": []map[string]any{{"loc": []string{"body"}, "msg": msg, "type": "value_error"}},
	})
}

// newHandler 는 계약의 두 엔드포인트(POST /eta, GET /health)를 가진 핸들러를 만든다.
func newHandler(p Predictor, modelName string) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "model": modelName})
	})

	mux.HandleFunc("POST /eta", func(w http.ResponseWriter, r *http.Request) {
		// FastAPI 와 같이 JSON 이 아닌 Content-Type 은 거절한다.
		if mt, _, err := mime.ParseMediaType(r.Header.Get("Content-Type")); err != nil ||
			!(mt == "application/json" || strings.HasSuffix(mt, "+json")) {
			writeUnprocessable(w, "Content-Type 은 application/json 이어야 합니다")
			return
		}
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBodyBytes))
		if err != nil {
			writeUnprocessable(w, "요청 본문을 읽을 수 없습니다")
			return
		}
		distanceKm, pendingOrders, err := parseEtaRequest(bytes.TrimSpace(body))
		if err != nil {
			writeUnprocessable(w, err.Error())
			return
		}
		minutes, err := p.Predict(distanceKm, pendingOrders)
		if err != nil {
			// 모델 실행 자체가 실패한 것은 입력 탓이 아니므로 500 이다.
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "예측에 실패했습니다"})
			return
		}
		// 응답 계약(minutes: 유한한 숫자)을 지킬 수 없으면 null 을 내보내지 않고 거절한다.
		if math.IsNaN(minutes) || math.IsInf(minutes, 0) {
			writeUnprocessable(w, "계산 결과가 유효한 숫자가 아닙니다")
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"minutes": minutes, "model": modelName})
	})

	return mux
}
