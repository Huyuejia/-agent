package server

import (
	"bytes"
	"context"
	"crypto/rsa"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"customer-intelligence-workbench/gateway/internal/config"
	"customer-intelligence-workbench/gateway/internal/ratelimit"
)

type fakeLimiter struct {
	result   ratelimit.Result
	err      error
	identity string
	limit    int
	window   time.Duration
}

func (limiter *fakeLimiter) Allow(_ context.Context, identity string, limit int, window time.Duration) (ratelimit.Result, error) {
	limiter.identity = identity
	limiter.limit = limit
	limiter.window = window
	return limiter.result, limiter.err
}

func TestRateLimitReturns429BeforeProxy(t *testing.T) {
	upstreamCalls := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		upstreamCalls++
		writer.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()
	limiter := &fakeLimiter{result: ratelimit.Result{Allowed: false, RetryAfter: 1500 * time.Millisecond}}
	handler := gatewayWithLimiter(t, upstream.URL, limiter, nil)
	request := httptest.NewRequest(http.MethodPost, "/api/auth/login", nil)
	request.RemoteAddr = "203.0.113.7:1234"
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusTooManyRequests || recorder.Header().Get("Retry-After") != "2" {
		t.Fatalf("response = %d, headers = %#v", recorder.Code, recorder.Header())
	}
	if recorder.Header().Get("X-RateLimit-Limit") != "3" || recorder.Header().Get("X-RateLimit-Remaining") != "0" {
		t.Fatalf("rate headers = %#v", recorder.Header())
	}
	if upstreamCalls != 0 || !strings.HasPrefix(limiter.identity, "ip:") || !strings.HasSuffix(limiter.identity, ":auth") {
		t.Fatalf("calls = %d, identity = %q", upstreamCalls, limiter.identity)
	}
}

func TestPrivateRateLimitUsesJWTSubjectAndChatPolicy(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	limiter := &fakeLimiter{result: ratelimit.Result{Allowed: true, Remaining: 4}}
	handler, privateKey := gatewayWithLimiterAndKey(t, upstream.URL, limiter, nil)
	request := httptest.NewRequest(http.MethodPost, "/api/chat", nil)
	request.Header.Set("Authorization", "Bearer "+serverTestToken(t, privateKey, "42"))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusNoContent || limiter.identity != "user:42:chat" || limiter.limit != 5 || limiter.window != time.Minute {
		t.Fatalf("status = %d, identity = %q, limit = %d, window = %s", recorder.Code, limiter.identity, limiter.limit, limiter.window)
	}
}

func TestRateLimitRedisFailureFailsOpenAndLogs(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	limiter := &fakeLimiter{err: errors.New("redis unavailable")}
	var output bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&output, nil))
	handler := gatewayWithLimiter(t, upstream.URL, limiter, logger)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, "/api/auth/login", nil))

	if recorder.Code != http.StatusNoContent {
		t.Fatalf("status = %d", recorder.Code)
	}
	if !strings.Contains(output.String(), `"msg":"rate_limit_fail_open"`) {
		t.Fatalf("log = %s", output.String())
	}
}

func gatewayWithLimiter(t *testing.T, upstreamURL string, limiter RequestLimiter, logger *slog.Logger) *Gateway {
	handler, _ := gatewayWithLimiterAndKey(t, upstreamURL, limiter, logger)
	return handler
}

func gatewayWithLimiterAndKey(t *testing.T, upstreamURL string, limiter RequestLimiter, logger *slog.Logger) (*Gateway, *rsa.PrivateKey) {
	t.Helper()
	privateKey, verifier := serverTestVerifier(t)
	handler, err := New(config.Config{
		UpstreamURL: upstreamURL, UpstreamTimeout: time.Second, ReadinessTimeout: time.Second,
		RateLimitWindow: time.Minute, RateLimitAuth: 3, RateLimitChat: 5, RateLimitUser: 10,
	}, verifier, limiter, logger)
	if err != nil {
		t.Fatal(err)
	}
	return handler, privateKey
}
