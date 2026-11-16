// ONNX Runtime 으로 모델을 실행하는 Predictor.
//
// 구조에서의 위치
//   - 누가 부르나: main.go 가 시작할 때 한 번 만들고, eta.go 의 핸들러가 요청마다 Predict 를 부른다.
//   - 무엇을 부르나: github.com/yalue/onnxruntime_go (cgo 로 공유 라이브러리 libonnxruntime.so 를 dlopen).
//   - 무엇을 읽나: artifacts/model_sklearn.onnx (입력 "X" [1, 2] float32 → 출력 "variable" [1, 1] float32).
//     이 이름 · 모양은 training/export_onnx.py 와 맞춘 계약이다. 정규화는 모델 안에 있다.
package main

import (
	"fmt"
	"sync"

	ort "github.com/yalue/onnxruntime_go"
)

// onnxPredictor 는 입력 · 출력 텐서를 한 번만 만들어 재사용한다.
// 텐서를 공유하므로 Run 을 한 번에 하나씩만 하도록 mutex 로 막는다 (모델이 작아 병목이 되지 않는다).
type onnxPredictor struct {
	mu      sync.Mutex
	session *ort.AdvancedSession
	input   *ort.Tensor[float32]
	output  *ort.Tensor[float32]
}

// newONNXPredictor 는 공유 라이브러리를 불러와 모델 세션을 만든다.
func newONNXPredictor(libPath, modelPath string) (*onnxPredictor, error) {
	ort.SetSharedLibraryPath(libPath)
	if err := ort.InitializeEnvironment(); err != nil {
		return nil, fmt.Errorf("ONNX Runtime 초기화 실패 (%s): %w", libPath, err)
	}
	input, err := ort.NewEmptyTensor[float32](ort.NewShape(1, 2))
	if err != nil {
		return nil, err
	}
	output, err := ort.NewEmptyTensor[float32](ort.NewShape(1, 1))
	if err != nil {
		return nil, err
	}
	session, err := ort.NewAdvancedSession(modelPath,
		[]string{"X"}, []string{"variable"},
		[]ort.Value{input}, []ort.Value{output}, nil)
	if err != nil {
		return nil, fmt.Errorf("모델 불러오기 실패 (%s): %w", modelPath, err)
	}
	return &onnxPredictor{session: session, input: input, output: output}, nil
}

// Predict 는 float32 로 바꿔 모델에 넣고, 결과(분)를 float64 로 돌려준다.
// Python 구현체(np.array(..., dtype=np.float32))와 같은 변환이다.
func (p *onnxPredictor) Predict(distanceKm, pendingOrders float64) (float64, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	data := p.input.GetData()
	data[0] = float32(distanceKm)
	data[1] = float32(pendingOrders)
	if err := p.session.Run(); err != nil {
		return 0, err
	}
	return float64(p.output.GetData()[0]), nil
}
