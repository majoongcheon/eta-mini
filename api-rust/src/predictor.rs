//! ONNX Runtime 으로 모델을 실행하는 Predictor.
//!
//! 구조에서의 위치
//!   - 누가 부르나: main.rs 가 시작할 때 한 번 만들고, eta.rs 의 핸들러가 요청마다 `predict` 를 부른다.
//!   - 무엇을 부르나: ort 크레이트 (load-dynamic 으로 공유 라이브러리 libonnxruntime.so 를 실행 중에 연다).
//!   - 무엇을 읽나: artifacts/model_sklearn.onnx (입력 "X" [1, 2] float32 → 출력 "variable" [1, 1] float32).
//!     이 이름 · 모양은 training/export_onnx.py 와 맞춘 계약이다. 정규화는 모델 안에 있다.

use std::sync::Mutex;

use ort::environment::Environment;
use ort::session::Session;
use ort::value::Tensor;

use crate::eta::Predictor;

/// Session::run 은 &mut self 라서 Mutex 로 한 번에 하나씩 실행한다 (모델이 작아 병목이 되지 않는다).
pub struct OnnxPredictor {
    /// 세션보다 오래 살아야 하므로 함께 들고 있는다.
    _environment: Environment,
    session: Mutex<Session>,
}

impl OnnxPredictor {
    /// 공유 라이브러리를 불러와 모델 세션을 만든다.
    pub fn new(lib_path: &str, model_path: &str) -> Result<Self, String> {
        let init_error = |e: &dyn std::fmt::Display| format!("ONNX Runtime 초기화 실패 ({lib_path}): {e}");
        let environment = ort::init_from(lib_path)
            .map_err(|e| init_error(&e))?
            .build()
            .map_err(|e| init_error(&e))?;
        let session = Session::builder(&environment)
            .and_then(|mut b| b.commit_from_file(model_path))
            .map_err(|e| format!("모델 불러오기 실패 ({model_path}): {e}"))?;
        Ok(Self { _environment: environment, session: Mutex::new(session) })
    }
}

impl Predictor for OnnxPredictor {
    /// float32 로 바꿔 모델에 넣고, 결과(분)를 f64 로 돌려준다.
    /// Python 구현체(np.array(..., dtype=np.float32))와 같은 변환이다.
    fn predict(&self, distance_km: f64, pending_orders: f64) -> Result<f64, String> {
        let input = Tensor::from_array(([1usize, 2], vec![distance_km as f32, pending_orders as f32]))
            .map_err(|e| e.to_string())?;
        let mut session = self.session.lock().map_err(|_| "세션 잠금 실패".to_string())?;
        let outputs = session.run(ort::inputs!["X" => input]).map_err(|e| e.to_string())?;
        let (_, data) = outputs["variable"].try_extract_tensor::<f32>().map_err(|e| e.to_string())?;
        data.first().map(|v| *v as f64).ok_or_else(|| "모델 출력이 비어 있다".to_string())
    }
}
