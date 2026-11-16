//! 입력 검증 + HTTP 핸들러. api/app.py 와 같은 API 계약(AGENTS.md 「API 계약」)을 지킨다.
//!
//! 구조에서의 위치
//!   - 누가 부르나: main.rs 가 `router` 로 라우터를 만들어 서버에 연결한다. 아래 테스트는 가짜 Predictor 로 직접 부른다.
//!   - 무엇을 부르나: `Predictor::predict` (실제로는 predictor.rs 의 ONNX 구현).
//!   - 무엇을 저장하나: 아무것도 저장하지 않는다.
//!
//! 계약 요약
//!   - POST /eta: {"distance_km": 0~20 숫자, "pending_orders": 0 이상 정수} 두 필드만.
//!     숫자가 아님 · 따옴표 친 숫자 · true/false · 소수인 주문 수 · 음수 · NaN/무한대 · 20km 초과 ·
//!     빠진 필드 · 모르는 필드 · 계산이 넘치는 큰 값은 422. 500 이나 minutes: null 은 계약 위반.
//!   - axum 의 Json 추출기는 형식 오류에 400 · 415 를 돌려주므로 쓰지 않고, 본문을 직접 파싱해
//!     모든 입력 오류를 422 로 맞춘다.
//!   - GET /health: {"status": "ok", "model": 구현체 이름}

use std::collections::HashMap;
use std::sync::Arc;

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{json, value::RawValue};

/// 거리 상한(km). api/app.py 의 MAX_DISTANCE_KM 과 같은 값이다 (2026-11-12 사용자 지시로 정한 계약).
const MAX_DISTANCE_KM: f64 = 20.0;

/// 요청 본문 크기 상한. 두 숫자만 받으므로 1 MiB 면 충분하다.
const MAX_BODY_BYTES: usize = 1 << 20;

/// 배달 예상 시간(분)을 계산한다. 인자는 검증을 마친 값이다.
pub trait Predictor: Send + Sync {
    fn predict(&self, distance_km: f64, pending_orders: f64) -> Result<f64, String>;
}

#[derive(Clone)]
struct AppState {
    predictor: Arc<dyn Predictor>,
    model_name: Arc<str>,
}

/// 계약의 두 엔드포인트(POST /eta, GET /health)를 가진 라우터를 만든다.
pub fn router(predictor: Arc<dyn Predictor>, model_name: String) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/eta", post(eta))
        .with_state(AppState { predictor, model_name: model_name.into() })
}

/// 422 응답. 모양은 FastAPI 와 같이 {"detail": [{"loc", "msg", "type"}]} 이다.
fn unprocessable(msg: &str) -> Response {
    let body = json!({ "detail": [{ "loc": ["body"], "msg": msg, "type": "value_error" }] });
    (StatusCode::UNPROCESSABLE_ENTITY, Json(body)).into_response()
}

/// 정수 리터럴인가 (JSON 숫자 중 소수점 · 지수가 없는 것). "2.0" · "2e0" 은 정수로 보지 않는다 (pydantic strict 와 같다).
fn is_int_literal(s: &str) -> bool {
    let digits = s.strip_prefix('-').unwrap_or(s);
    match digits.as_bytes() {
        [b'0'] => true,
        [b'1'..=b'9', rest @ ..] => rest.iter().all(u8::is_ascii_digit),
        _ => false,
    }
}

/// JSON 숫자만 받아들인다. 문자열("3") · true/false · null 은 거절한다.
fn parse_number(raw: &RawValue, name: &str) -> Result<f64, String> {
    let text = raw.get();
    // 본문 전체가 유효한 JSON 이므로, 숫자나 '-' 로 시작하면 JSON 숫자다.
    if !matches!(text.as_bytes().first(), Some(b'-' | b'0'..=b'9')) {
        return Err(format!("{name} 는 숫자여야 합니다"));
    }
    let v: f64 = text.parse().map_err(|_| format!("{name} 는 숫자여야 합니다"))?;
    if !v.is_finite() {
        return Err(format!("{name} 는 유한한 숫자여야 합니다")); // 1e999 처럼 넘치는 값
    }
    Ok(v)
}

/// 요청 본문을 검증해 (거리, 주문 수)를 돌려준다. 계약 위반이면 오류 메시지.
fn parse_eta_request(body: &[u8]) -> Result<(f64, f64), String> {
    // 객체가 아니거나(배열 · 숫자 · null …) JSON 이 깨졌으면 여기서 걸러진다.
    let fields: HashMap<String, Box<RawValue>> =
        serde_json::from_slice(body).map_err(|_| "요청 본문은 JSON 객체여야 합니다".to_string())?;
    // 모르는 필드(철자 틀린 필드 포함)는 조용히 무시하지 않고 거절한다.
    for name in fields.keys() {
        if name != "distance_km" && name != "pending_orders" {
            return Err(format!("모르는 필드: {name}"));
        }
    }
    let raw_distance = fields.get("distance_km").ok_or("distance_km 가 없습니다")?;
    let raw_orders = fields.get("pending_orders").ok_or("pending_orders 가 없습니다")?;

    let distance_km = parse_number(raw_distance, "distance_km")?;
    if !(0.0..=MAX_DISTANCE_KM).contains(&distance_km) {
        return Err("distance_km 는 0 이상 20 이하여야 합니다".to_string());
    }

    let orders_text = raw_orders.get();
    if !is_int_literal(orders_text) {
        return Err("pending_orders 는 정수여야 합니다".to_string());
    }
    let pending_orders: f64 = orders_text.parse().map_err(|_| "pending_orders 는 정수여야 합니다".to_string())?;
    if pending_orders < 0.0 {
        return Err("pending_orders 는 0 이상이어야 합니다".to_string());
    }
    if pending_orders.is_infinite() {
        return Err("입력이 너무 커서 계산할 수 없습니다".to_string()); // 10**400 처럼 실수로도 못 나타내는 값
    }
    // "-0" 은 0 이다 (음수 0 이 모델에 들어가지 않게 한다).
    Ok((distance_km + 0.0, pending_orders + 0.0))
}

async fn health(State(state): State<AppState>) -> Response {
    Json(json!({ "status": "ok", "model": &*state.model_name })).into_response()
}

async fn eta(State(state): State<AppState>, req: Request<Body>) -> Response {
    // FastAPI 와 같이 JSON 이 아닌 Content-Type 은 거절한다.
    let content_type = req
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.split(';').next())
        .map(|v| v.trim().to_ascii_lowercase())
        .unwrap_or_default();
    if !(content_type == "application/json" || content_type.ends_with("+json")) {
        return unprocessable("Content-Type 은 application/json 이어야 합니다");
    }
    let Ok(body) = axum::body::to_bytes(req.into_body(), MAX_BODY_BYTES).await else {
        return unprocessable("요청 본문을 읽을 수 없습니다");
    };
    let (distance_km, pending_orders) = match parse_eta_request(&body) {
        Ok(v) => v,
        Err(msg) => return unprocessable(&msg),
    };
    let minutes = match state.predictor.predict(distance_km, pending_orders) {
        Ok(m) => m,
        Err(e) => {
            // 모델 실행 자체가 실패한 것은 입력 탓이 아니므로 500 이다.
            eprintln!("예측 실패: {e}");
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "detail": "예측에 실패했습니다" })))
                .into_response();
        }
    };
    // 응답 계약(minutes: 유한한 숫자)을 지킬 수 없으면 null 을 내보내지 않고 거절한다.
    if !minutes.is_finite() {
        return unprocessable("계산 결과가 유효한 숫자가 아닙니다");
    }
    Json(json!({ "minutes": minutes, "model": &*state.model_name })).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use tower::ServiceExt;

    /// 규칙 구현체와 같은 식(10 + 3km + 2건)을 쓰는 가짜 Predictor. ONNX 없이 계약만 시험한다.
    struct Rule;
    impl Predictor for Rule {
        fn predict(&self, d: f64, n: f64) -> Result<f64, String> {
            Ok(10.0 + 3.0 * d + 2.0 * n)
        }
    }

    /// 항상 무한대를 돌려주는 Predictor (계산 결과가 유효하지 않은 경우).
    struct Inf;
    impl Predictor for Inf {
        fn predict(&self, _: f64, _: f64) -> Result<f64, String> {
            Ok(f64::INFINITY)
        }
    }

    async fn call(
        p: Arc<dyn Predictor>,
        method: &str,
        uri: &str,
        ctype: Option<&str>,
        body: &str,
    ) -> (u16, serde_json::Value) {
        let mut b = axum::http::Request::builder().method(method).uri(uri);
        if let Some(c) = ctype {
            b = b.header("content-type", c);
        }
        let resp = router(p, "test".into()).oneshot(b.body(Body::from(body.to_string())).unwrap()).await.unwrap();
        let status = resp.status().as_u16();
        let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20).await.unwrap();
        (status, serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null))
    }

    async fn post_eta(body: &str) -> (u16, serde_json::Value) {
        call(Arc::new(Rule), "POST", "/eta", Some("application/json"), body).await
    }

    #[tokio::test]
    async fn 정상_입력은_200_과_예상값() {
        let (s, v) = post_eta(r#"{"distance_km": 3, "pending_orders": 2}"#).await;
        assert_eq!(s, 200);
        assert_eq!(v, json!({"minutes": 23.0, "model": "test"}));
    }

    #[tokio::test]
    async fn 경계값_0km_와_20km_는_허용() {
        assert_eq!(post_eta(r#"{"distance_km": 0, "pending_orders": 0}"#).await.0, 200);
        assert_eq!(post_eta(r#"{"distance_km": 20, "pending_orders": 0}"#).await.0, 200);
        assert_eq!(post_eta(r#"{"distance_km": 2e1, "pending_orders": 0}"#).await.0, 200);
        assert_eq!(post_eta(r#"{"distance_km": 2.5e1, "pending_orders": 0}"#).await.0, 422);
        assert_eq!(post_eta(r#"{"distance_km": 1, "pending_orders": -0}"#).await.0, 200);
    }

    #[tokio::test]
    async fn 잘못된_입력은_모두_422() {
        let cases = [
            r#"{"distance_km": "3", "pending_orders": 2}"#,       // 따옴표 친 숫자
            r#"{"distance_km": true, "pending_orders": 2}"#,      // 불리언
            r#"{"distance_km": null, "pending_orders": 2}"#,      // null
            r#"{"distance_km": 3, "pending_orders": 2.5}"#,       // 소수 주문 수
            r#"{"distance_km": 3, "pending_orders": 2.0}"#,       // 2.0 도 정수가 아니다
            r#"{"distance_km": 3, "pending_orders": "2"}"#,
            r#"{"distance_km": 3, "pending_orders": false}"#,
            r#"{"distance_km": -1, "pending_orders": 2}"#,        // 음수 거리
            r#"{"distance_km": 3, "pending_orders": -1}"#,        // 음수 주문 수
            r#"{"distance_km": 20.001, "pending_orders": 0}"#,    // 상한 초과
            r#"{"distance_km": 1e999, "pending_orders": 0}"#,     // 넘치는 값
            r#"{"distance_km": NaN, "pending_orders": 0}"#,       // JSON 이 아님
            r#"{"distance_km": Infinity, "pending_orders": 0}"#,
            r#"{"distance_km": 3}"#,                              // 빠진 필드
            r#"{"pending_orders": 2}"#,
            r#"{"distance_km": 3, "pending_orders": 2, "x": 1}"#, // 모르는 필드
            r#"{"distance_km": 3, "pending_orders": 2, "pending_order": 1}"#,
            r#"[]"#, r#"null"#, r#"3"#, r#""""#, r#"{"#, r#""#,   // 객체가 아님 · 깨진 JSON
        ];
        for body in cases {
            let (s, v) = post_eta(body).await;
            assert_eq!(s, 422, "본문 {body:?}");
            assert!(v["detail"].is_array(), "본문 {body:?}");
        }
    }

    #[tokio::test]
    async fn 계산이_넘치는_큰_주문_수는_422() {
        let huge = format!(r#"{{"distance_km": 1, "pending_orders": 1{}}}"#, "0".repeat(400));
        assert_eq!(post_eta(&huge).await.0, 422);
        // 모델 결과가 무한대면 null 대신 422.
        let ok = r#"{"distance_km": 1, "pending_orders": 1}"#;
        assert_eq!(call(Arc::new(Inf), "POST", "/eta", Some("application/json"), ok).await.0, 422);
    }

    #[tokio::test]
    async fn json_이_아닌_content_type_과_없는_content_type_은_422() {
        let body = r#"{"distance_km": 3, "pending_orders": 2}"#;
        assert_eq!(call(Arc::new(Rule), "POST", "/eta", Some("text/plain"), body).await.0, 422);
        assert_eq!(call(Arc::new(Rule), "POST", "/eta", None, body).await.0, 422);
        assert_eq!(call(Arc::new(Rule), "POST", "/eta", Some("application/json; charset=utf-8"), body).await.0, 200);
    }

    #[tokio::test]
    async fn health_는_200_과_구현체_이름() {
        let (s, v) = call(Arc::new(Rule), "GET", "/health", None, "").await;
        assert_eq!(s, 200);
        assert_eq!(v, json!({"status": "ok", "model": "test"}));
    }
}
