// eta.go 의 API 계약 테스트. ONNX Runtime 없이 가짜 Predictor 로 입력 검증 · 응답 형식 · 상태 코드를 확인한다.
//
// 구조에서의 위치: `go test ./...` 가 부른다. 실제 서버를 띄우거나 네트워크를 쓰지 않고 httptest 만 쓴다.
// 정답 기준은 AGENTS.md 「API 계약」이다 (구현이 아니라 계약을 테스트한다).
package main

import (
	"encoding/json"
	"errors"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// fakeRule 은 사장님 규칙(10 + 3·km + 2·건)으로 계산하는 가짜 예측기다. 입력 검증이 목적이라 모델은 중요하지 않다.
type fakeRule struct{}

func (fakeRule) Predict(d, n float64) (float64, error) { return 10 + 3*d + 2*n, nil }

// fakeFunc 는 원하는 결과를 돌려주는 가짜 예측기다.
type fakeFunc func(d, n float64) (float64, error)

func (f fakeFunc) Predict(d, n float64) (float64, error) { return f(d, n) }

func post(t *testing.T, p Predictor, contentType, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/eta", strings.NewReader(body))
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	rec := httptest.NewRecorder()
	newHandler(p, "go-test").ServeHTTP(rec, req)
	return rec
}

// TestHealth: GET /health 는 200 과 {"status": "ok", "model": 구현체 이름} 을 돌려준다.
func TestHealth(t *testing.T) {
	rec := httptest.NewRecorder()
	newHandler(fakeRule{}, "go-test").ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/health", nil))
	if rec.Code != 200 {
		t.Fatalf("상태 코드 %d, 기대 200", rec.Code)
	}
	var got map[string]string
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if got["status"] != "ok" || got["model"] != "go-test" || len(got) != 2 {
		t.Errorf("응답 %v", got)
	}
}

// TestEtaValid: 정상 입력과 경계값(0km · 0건 · 20km 상한 자체 · 큰 주문 수)은 200 과 예상값을 돌려준다.
func TestEtaValid(t *testing.T) {
	cases := []struct {
		name, body string
		want       float64
	}{
		{"알려진 입력", `{"distance_km": 3, "pending_orders": 2}`, 23},
		{"소수 거리", `{"distance_km": 2.5, "pending_orders": 0}`, 17.5},
		{"거리 0 · 주문 0", `{"distance_km": 0, "pending_orders": 0}`, 10},
		{"거리 상한 20km", `{"distance_km": 20, "pending_orders": 0}`, 70},
		{"거리 20.0 표기", `{"distance_km": 20.0, "pending_orders": 1}`, 72},
		{"주문 수가 큼", `{"distance_km": 1, "pending_orders": 1000000}`, 2000013},
		{"필드 순서 바뀜 · 공백", ` {"pending_orders": 2 ,"distance_km":3}`, 23},
		{"-0 은 0", `{"distance_km": -0, "pending_orders": -0}`, 10},
	}
	for _, c := range cases {
		rec := post(t, fakeRule{}, "application/json", c.body)
		if rec.Code != 200 {
			t.Errorf("%s: 상태 코드 %d (%s)", c.name, rec.Code, rec.Body.String())
			continue
		}
		var got struct {
			Minutes *float64 `json:"minutes"`
			Model   string   `json:"model"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil || got.Minutes == nil {
			t.Errorf("%s: minutes 가 숫자가 아님 (%s)", c.name, rec.Body.String())
			continue
		}
		if math.Abs(*got.Minutes-c.want) > 1e-9 || got.Model != "go-test" {
			t.Errorf("%s: 응답 %+v, 기대 minutes=%v", c.name, got, c.want)
		}
	}
}

// TestEtaInvalid: 계약이 정한 잘못된 입력은 모두 422 이고, 예측기는 부르지 않는다.
func TestEtaInvalid(t *testing.T) {
	cases := map[string]string{
		"거리가 문자열":        `{"distance_km": "abc", "pending_orders": 1}`,
		"따옴표 친 숫자(거리)":   `{"distance_km": "3", "pending_orders": 1}`,
		"따옴표 친 숫자(주문)":   `{"distance_km": 3, "pending_orders": "1"}`,
		"거리가 true":       `{"distance_km": true, "pending_orders": 1}`,
		"주문이 false":      `{"distance_km": 3, "pending_orders": false}`,
		"거리가 null":       `{"distance_km": null, "pending_orders": 1}`,
		"소수인 주문 수":       `{"distance_km": 3, "pending_orders": 1.5}`,
		"소수점이 붙은 정수":     `{"distance_km": 3, "pending_orders": 2.0}`,
		"지수 표기 주문 수":     `{"distance_km": 3, "pending_orders": 1e2}`,
		"음수 거리":          `{"distance_km": -1, "pending_orders": 1}`,
		"음수 주문 수":        `{"distance_km": 3, "pending_orders": -1}`,
		"거리 상한 초과":       `{"distance_km": 20.01, "pending_orders": 1}`,
		"거리 지수 표기로 초과":   `{"distance_km": 1e2, "pending_orders": 1}`,
		"거리가 넘침(1e999)":  `{"distance_km": 1e999, "pending_orders": 1}`,
		"NaN":            `{"distance_km": NaN, "pending_orders": 1}`,
		"Infinity":       `{"distance_km": Infinity, "pending_orders": 1}`,
		"거리 필드 없음":       `{"pending_orders": 1}`,
		"주문 필드 없음":       `{"distance_km": 3}`,
		"빈 객체":           `{}`,
		"모르는 필드":         `{"distance_km": 3, "pending_orders": 1, "extra": 1}`,
		"철자 틀린 필드":       `{"distance": 3, "pending_orders": 1}`,
		"계산이 넘치는 큰 주문 수": `{"distance_km": 3, "pending_orders": 1` + strings.Repeat("0", 400) + `}`,
		"JSON 이 아님":      `not json`,
		"빈 본문":           ``,
		"배열":             `[3, 1]`,
		"null 본문":        `null`,
		"숫자 본문":          `5`,
		"깨진 JSON":        `{"distance_km": 3,`,
		"선행 0 이 붙은 주문 수": `{"distance_km": 3, "pending_orders": 01}`,
	}
	called := false
	spy := fakeFunc(func(d, n float64) (float64, error) { called = true; return 1, nil })
	for name, body := range cases {
		called = false
		rec := post(t, spy, "application/json", body)
		if rec.Code != http.StatusUnprocessableEntity {
			t.Errorf("%s: 상태 코드 %d, 기대 422 (%s)", name, rec.Code, rec.Body.String())
		}
		if called {
			t.Errorf("%s: 잘못된 입력인데 예측기가 불렸다", name)
		}
		var detail map[string]any
		if err := json.Unmarshal(rec.Body.Bytes(), &detail); err != nil || detail["detail"] == nil {
			t.Errorf("%s: 422 본문이 {\"detail\": ...} 모양이 아님 (%s)", name, rec.Body.String())
		}
	}
}

// TestEtaContentType: JSON 이 아닌 Content-Type 은 422 이고, application/json 에 charset 이 붙어도 받는다.
func TestEtaContentType(t *testing.T) {
	body := `{"distance_km": 3, "pending_orders": 2}`
	for _, ct := range []string{"", "text/plain", "application/x-www-form-urlencoded"} {
		if rec := post(t, fakeRule{}, ct, body); rec.Code != 422 {
			t.Errorf("Content-Type %q: 상태 코드 %d, 기대 422", ct, rec.Code)
		}
	}
	if rec := post(t, fakeRule{}, "application/json; charset=utf-8", body); rec.Code != 200 {
		t.Errorf("charset 이 붙은 application/json: 상태 코드 %d, 기대 200", rec.Code)
	}
}

// TestEtaNonFiniteResult: 예측 결과가 NaN · 무한대면 null 을 내보내지 않고 422 로 거절한다.
func TestEtaNonFiniteResult(t *testing.T) {
	for _, v := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		p := fakeFunc(func(d, n float64) (float64, error) { return v, nil })
		rec := post(t, p, "application/json", `{"distance_km": 3, "pending_orders": 2}`)
		if rec.Code != 422 {
			t.Errorf("결과 %v: 상태 코드 %d, 기대 422 (%s)", v, rec.Code, rec.Body.String())
		}
		if strings.Contains(rec.Body.String(), "null") {
			t.Errorf("결과 %v: 본문에 null (%s)", v, rec.Body.String())
		}
	}
}

// TestEtaPredictorError: 모델 실행이 실패하면(입력 탓이 아님) 500 이다.
func TestEtaPredictorError(t *testing.T) {
	p := fakeFunc(func(d, n float64) (float64, error) { return 0, errors.New("ort 실패") })
	if rec := post(t, p, "application/json", `{"distance_km": 3, "pending_orders": 2}`); rec.Code != 500 {
		t.Errorf("상태 코드 %d, 기대 500", rec.Code)
	}
}

// TestMethodNotAllowed: 계약에 없는 메서드는 405 이다 (GET /eta · POST /health).
func TestMethodNotAllowed(t *testing.T) {
	h := newHandler(fakeRule{}, "go-test")
	for _, c := range []struct{ method, path string }{{"GET", "/eta"}, {"POST", "/health"}} {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest(c.method, c.path, nil))
		if rec.Code != http.StatusMethodNotAllowed {
			t.Errorf("%s %s: 상태 코드 %d, 기대 405", c.method, c.path, rec.Code)
		}
	}
}
