// delivery-eta Go 서비스. api/app.py(Python) 와 같은 API 계약으로 같은 ONNX 모델을 서빙한다.
//
// 구조에서의 위치
//   - 누가 부르나: api-go/Dockerfile 의 ENTRYPOINT. training/check_parity.py 가 이 서비스의 예측을
//     Python API 와 비교한다.
//   - 무엇을 부르나: newONNXPredictor(predictor.go) 로 모델을 불러오고, newHandler(eta.go) 로 요청을 처리한다.
//   - 무엇을 저장하나: 아무것도 저장하지 않는다.
//
// 환경변수 (모두 선택)
//   - PORT              듣는 포트. 기본 8080
//   - MODEL_PATH        ONNX 모델 파일. 기본 artifacts/model_sklearn.onnx
//   - ONNXRUNTIME_LIB   ONNX Runtime 공유 라이브러리. 기본 /usr/local/lib/libonnxruntime.so
//   - MODEL_IMPL        응답의 "model" 에 담을 구현체 이름. 기본 go-onnx
package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	predictor, err := newONNXPredictor(
		env("ONNXRUNTIME_LIB", "/usr/local/lib/libonnxruntime.so"),
		env("MODEL_PATH", "artifacts/model_sklearn.onnx"),
	)
	if err != nil {
		log.Fatalf("시작 실패: %v", err)
	}

	server := &http.Server{
		Addr:              ":" + env("PORT", "8080"),
		Handler:           newHandler(predictor, env("MODEL_IMPL", "go-onnx")),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
	}

	// docker stop(SIGTERM) 을 받으면 처리 중인 요청을 마친 뒤 끝낸다.
	stop, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	go func() {
		<-stop.Done()
		ctx, done := context.WithTimeout(context.Background(), 10*time.Second)
		defer done()
		_ = server.Shutdown(ctx)
	}()

	log.Printf("듣는 중 %s", server.Addr)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}
