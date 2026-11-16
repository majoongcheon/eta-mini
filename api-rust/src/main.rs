//! delivery-eta Rust 서비스. api/app.py(Python) 와 같은 API 계약으로 같은 ONNX 모델을 서빙한다.
//!
//! 구조에서의 위치
//!   - 누가 부르나: api-rust/Dockerfile 의 ENTRYPOINT. training/check_parity.py 가 이 서비스의 예측을
//!     Python API 와 비교한다.
//!   - 무엇을 부르나: OnnxPredictor(predictor.rs)로 모델을 불러오고, eta::router(eta.rs)로 요청을 처리한다.
//!   - 무엇을 저장하나: 아무것도 저장하지 않는다.
//!
//! 환경변수 (모두 선택)
//!   - PORT         듣는 포트. 기본 8080
//!   - MODEL_PATH   ONNX 모델 파일. 기본 artifacts/model_sklearn.onnx
//!   - ORT_LIB      ONNX Runtime 공유 라이브러리. 기본 /usr/local/lib/libonnxruntime.so
//!   - MODEL_IMPL   응답의 "model" 에 담을 구현체 이름. 기본 rust-onnx

mod eta;
mod predictor;

use std::sync::Arc;

fn env_or(key: &str, fallback: &str) -> String {
    match std::env::var(key) {
        Ok(v) if !v.is_empty() => v,
        _ => fallback.to_string(),
    }
}

/// Ctrl-C 나 docker stop(SIGTERM)을 받으면 처리 중인 요청을 마치고 끝낸다.
async fn shutdown_signal() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    #[cfg(unix)]
    let terminate = async {
        if let Ok(mut s) = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            s.recv().await;
        }
    };
    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();
    tokio::select! { _ = ctrl_c => {}, _ = terminate => {} }
}

#[tokio::main]
async fn main() {
    let predictor = match predictor::OnnxPredictor::new(
        &env_or("ORT_LIB", "/usr/local/lib/libonnxruntime.so"),
        &env_or("MODEL_PATH", "artifacts/model_sklearn.onnx"),
    ) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("시작 실패: {e}");
            std::process::exit(1);
        }
    };

    let app = eta::router(Arc::new(predictor), env_or("MODEL_IMPL", "rust-onnx"));
    let addr = format!("0.0.0.0:{}", env_or("PORT", "8080"));
    let listener = match tokio::net::TcpListener::bind(&addr).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("시작 실패: {addr} 를 열 수 없다: {e}");
            std::process::exit(1);
        }
    };
    println!("듣는 중 {addr}");
    if let Err(e) = axum::serve(listener, app).with_graceful_shutdown(shutdown_signal()).await {
        eprintln!("서버 오류: {e}");
        std::process::exit(1);
    }
}
