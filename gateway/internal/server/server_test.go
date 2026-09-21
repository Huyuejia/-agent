package server

import (
	"bytes"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"customer-intelligence-workbench/gateway/internal/auth"
	"customer-intelligence-workbench/gateway/internal/config"
)

const (
	serverTestIssuer   = "test-issuer"
	serverTestAudience = "test-audience"
)

func TestPublicRouteRemovesSpoofedIdentityAndRequestID(t *testing.T) {
	var capturedUserID, capturedRequestID string
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		capturedUserID = request.Header.Get(userIDHeader)
		capturedRequestID = request.Header.Get(requestIDHeader)
		writeJSON(writer, http.StatusOK, map[string]string{"path": request.URL.Path})
	}))
	defer upstream.Close()

	handler, _ := newTestGateway(t, upstream.URL, 200*time.Millisecond, nil)
	request := httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader(`{}`))
	request.Header.Set(userIDHeader, "999")
	request.Header.Set(requestIDHeader, "client-controlled")
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
	if capturedUserID != "" {
		t.Fatalf("upstream user ID = %q, want empty", capturedUserID)
	}
	if capturedRequestID == "client-controlled" || len(capturedRequestID) != 32 {
		t.Fatalf("upstream request ID = %q", capturedRequestID)
	}
	if recorder.Header().Get(requestIDHeader) != capturedRequestID {
		t.Fatal("response and upstream request IDs differ")
	}
	if values := recorder.Result().Header.Values(requestIDHeader); len(values) != 1 {
		t.Fatalf("response request ID values = %#v, want exactly one", values)
	}
}

func TestPrivateRouteRequiresValidTokenAndForwardsTrustedIdentity(t *testing.T) {
	var calls atomic.Int32
	var capturedAuthorization, capturedUserID string
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		calls.Add(1)
		capturedAuthorization = request.Header.Get("Authorization")
		capturedUserID = request.Header.Get(userIDHeader)
		writer.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()

	handler, privateKey := newTestGateway(t, upstream.URL, 200*time.Millisecond, nil)
	unauthorized := httptest.NewRecorder()
	handler.ServeHTTP(unauthorized, httptest.NewRequest(http.MethodGet, "/api/conversations", nil))
	if unauthorized.Code != http.StatusUnauthorized || unauthorized.Header().Get("WWW-Authenticate") != "Bearer" {
		t.Fatalf("unauthorized response = %d, headers = %#v", unauthorized.Code, unauthorized.Header())
	}
	if calls.Load() != 0 {
		t.Fatal("unauthorized request reached upstream")
	}

	token := serverTestToken(t, privateKey, "42")
	request := httptest.NewRequest(http.MethodGet, "/api/conversations", nil)
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set(userIDHeader, "999")
	authorized := httptest.NewRecorder()
	handler.ServeHTTP(authorized, request)
	if authorized.Code != http.StatusNoContent {
		t.Fatalf("status = %d, body = %s", authorized.Code, authorized.Body.String())
	}
	if capturedAuthorization != "Bearer "+token || capturedUserID != "42" {
		t.Fatalf("upstream Authorization = %q, user ID = %q", capturedAuthorization, capturedUserID)
	}
}

func TestOptionsBypassesAuthentication(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	handler, _ := newTestGateway(t, upstream.URL, 200*time.Millisecond, nil)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodOptions, "/api/chat", nil))
	if recorder.Code != http.StatusNoContent {
		t.Fatalf("status = %d", recorder.Code)
	}
}

func TestHealthReadinessAndUnknownRoute(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/health" {
			t.Fatalf("readiness path = %q", request.URL.Path)
		}
		writer.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()
	handler, _ := newTestGateway(t, upstream.URL, 200*time.Millisecond, nil)

	for path, expected := range map[string]int{"/health": 200, "/ready": 200, "/unknown": 404} {
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code != expected {
			t.Fatalf("%s status = %d, want %d", path, recorder.Code, expected)
		}
	}
}

func TestReadinessFailsWhenUpstreamIsUnhealthy(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusInternalServerError)
	}))
	defer upstream.Close()
	handler, _ := newTestGateway(t, upstream.URL, 200*time.Millisecond, nil)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/ready", nil))
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d", recorder.Code)
	}
}

func TestUpstreamTimeoutReturnsGatewayTimeout(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		time.Sleep(75 * time.Millisecond)
		writer.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()
	handler, _ := newTestGateway(t, upstream.URL, 10*time.Millisecond, nil)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, "/api/auth/login", nil))
	if recorder.Code != http.StatusGatewayTimeout {
		t.Fatalf("status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
}

func TestStructuredLogContainsRequestMetadata(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		_, _ = io.WriteString(writer, "ok")
	}))
	defer upstream.Close()
	var output bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&output, nil))
	handler, _ := newTestGateway(t, upstream.URL, 200*time.Millisecond, logger)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, "/api/auth/login", nil))
	logLine := output.String()
	for _, expected := range []string{`"msg":"gateway_request"`, `"request_id":`, `"status":200`, `"bytes":2`} {
		if !strings.Contains(logLine, expected) {
			t.Fatalf("log %q does not contain %q", logLine, expected)
		}
	}
}

func TestNewRejectsInvalidInput(t *testing.T) {
	if _, err := New(config.Config{UpstreamURL: "http://example.test"}, nil, nil, nil); err == nil {
		t.Fatal("New() accepted nil verifier")
	}
	_, verifier := serverTestVerifier(t)
	if _, err := New(config.Config{UpstreamURL: "relative"}, verifier, nil, nil); err == nil {
		t.Fatal("New() accepted relative upstream URL")
	}
}

func newTestGateway(t *testing.T, upstreamURL string, timeout time.Duration, logger *slog.Logger) (*Gateway, *rsa.PrivateKey) {
	t.Helper()
	privateKey, verifier := serverTestVerifier(t)
	handler, err := New(config.Config{
		UpstreamURL: upstreamURL, UpstreamTimeout: timeout, ReadinessTimeout: timeout,
	}, verifier, nil, logger)
	if err != nil {
		t.Fatal(err)
	}
	return handler, privateKey
}

func serverTestVerifier(t *testing.T) (*rsa.PrivateKey, *auth.Verifier) {
	t.Helper()
	privateKey, err := rsa.GenerateKey(rand.Reader, 1024)
	if err != nil {
		t.Fatal(err)
	}
	publicDER, err := x509.MarshalPKIXPublicKey(&privateKey.PublicKey)
	if err != nil {
		t.Fatal(err)
	}
	publicPEM := pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: publicDER})
	verifier, err := auth.NewVerifier(publicPEM, serverTestIssuer, serverTestAudience)
	if err != nil {
		t.Fatal(err)
	}
	return privateKey, verifier
}

func serverTestToken(t *testing.T, privateKey *rsa.PrivateKey, subject string) string {
	t.Helper()
	encode := func(value any) string {
		data, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		return base64.RawURLEncoding.EncodeToString(data)
	}
	now := time.Now()
	unsigned := encode(map[string]any{"alg": "RS256", "typ": "JWT"}) + "." + encode(map[string]any{
		"sub": subject, "iat": now.Unix(), "exp": now.Add(time.Minute).Unix(),
		"iss": serverTestIssuer, "aud": serverTestAudience,
	})
	digest := sha256.Sum256([]byte(unsigned))
	signature, err := rsa.SignPKCS1v15(rand.Reader, privateKey, crypto.SHA256, digest[:])
	if err != nil {
		t.Fatal(err)
	}
	return unsigned + "." + base64.RawURLEncoding.EncodeToString(signature)
}
