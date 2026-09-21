package server

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"time"

	"customer-intelligence-workbench/gateway/internal/auth"
	"customer-intelligence-workbench/gateway/internal/config"
	"customer-intelligence-workbench/gateway/internal/ratelimit"
)

const (
	requestIDHeader = "X-Request-ID"
	userIDHeader    = "X-Authenticated-User-ID"
)

type RequestLimiter interface {
	Allow(context.Context, string, int, time.Duration) (ratelimit.Result, error)
}

type Gateway struct {
	config          config.Config
	upstream        *url.URL
	proxy           *httputil.ReverseProxy
	verifier        *auth.Verifier
	logger          *slog.Logger
	readinessClient *http.Client
	limiter         RequestLimiter
}

func New(config config.Config, verifier *auth.Verifier, limiter RequestLimiter, logger *slog.Logger) (*Gateway, error) {
	if verifier == nil {
		return nil, errors.New("JWT verifier must not be nil")
	}
	upstream, err := url.Parse(config.UpstreamURL)
	if err != nil || upstream.Scheme == "" || upstream.Host == "" {
		return nil, errors.New("UPSTREAM_URL must be an absolute HTTP URL")
	}
	if upstream.Scheme != "http" && upstream.Scheme != "https" {
		return nil, errors.New("UPSTREAM_URL must use http or https")
	}
	if logger == nil {
		logger = slog.Default()
	}

	proxy := httputil.NewSingleHostReverseProxy(upstream)
	originalDirector := proxy.Director
	proxy.Director = func(request *http.Request) {
		originalDirector(request)
		request.Host = upstream.Host
	}
	proxy.ModifyResponse = func(response *http.Response) error {
		response.Header.Set(requestIDHeader, response.Request.Header.Get(requestIDHeader))
		return nil
	}
	proxy.Transport = &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		DialContext:           (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		ForceAttemptHTTP2:     true,
		MaxIdleConns:          100,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   5 * time.Second,
		ResponseHeaderTimeout: config.UpstreamTimeout,
	}

	gateway := &Gateway{
		config:   config,
		upstream: upstream,
		proxy:    proxy,
		verifier: verifier,
		logger:   logger,
		limiter:  limiter,
		readinessClient: &http.Client{
			Timeout: config.ReadinessTimeout,
		},
	}
	proxy.ErrorHandler = gateway.proxyError
	return gateway, nil
}

func (gateway *Gateway) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	started := time.Now()
	requestID := newRequestID()
	request.Header.Del(userIDHeader)
	request.Header.Del(requestIDHeader)
	request.Header.Del("Forwarded")
	request.Header.Del("X-Forwarded-For")
	request.Header.Del("X-Real-IP")
	request.Header.Set(requestIDHeader, requestID)
	writer.Header().Set(requestIDHeader, requestID)

	recorder := &statusRecorder{ResponseWriter: writer, status: http.StatusOK}
	userID := ""
	defer func() {
		gateway.logger.Info(
			"gateway_request",
			"request_id", requestID,
			"method", request.Method,
			"path", request.URL.Path,
			"status", recorder.status,
			"bytes", recorder.bytes,
			"duration_ms", time.Since(started).Milliseconds(),
			"user_id", userID,
		)
	}()

	switch request.URL.Path {
	case "/health":
		writeJSON(recorder, http.StatusOK, map[string]string{"status": "ok"})
		return
	case "/ready":
		gateway.readiness(recorder, request)
		return
	}

	if !strings.HasPrefix(request.URL.Path, "/api/") {
		writeJSON(recorder, http.StatusNotFound, map[string]string{"detail": "not found"})
		return
	}
	if request.Method != http.MethodOptions && !isPublicAPI(request.URL.Path) {
		claims, err := gateway.verifier.VerifyBearer(request.Header.Get("Authorization"))
		if err != nil {
			recorder.Header().Set("WWW-Authenticate", "Bearer")
			writeJSON(recorder, http.StatusUnauthorized, map[string]string{"detail": "invalid or expired access token"})
			return
		}
		userID = claims.Subject
		request.Header.Set(userIDHeader, claims.Subject)
	}
	if request.Method != http.MethodOptions && gateway.limiter != nil {
		if gateway.enforceRateLimit(recorder, request, requestID, userID) {
			return
		}
	}

	contextWithTimeout, cancel := context.WithTimeout(request.Context(), gateway.config.UpstreamTimeout)
	defer cancel()
	recorder.Header().Del(requestIDHeader)
	gateway.proxy.ServeHTTP(recorder, request.WithContext(contextWithTimeout))
}

func (gateway *Gateway) readiness(writer http.ResponseWriter, request *http.Request) {
	healthURL := gateway.upstream.ResolveReference(&url.URL{Path: "/health"})
	upstreamRequest, err := http.NewRequestWithContext(request.Context(), http.MethodGet, healthURL.String(), nil)
	if err != nil {
		writeJSON(writer, http.StatusServiceUnavailable, map[string]string{"status": "not_ready"})
		return
	}
	response, err := gateway.readinessClient.Do(upstreamRequest)
	if err != nil {
		writeJSON(writer, http.StatusServiceUnavailable, map[string]string{"status": "not_ready"})
		return
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		writeJSON(writer, http.StatusServiceUnavailable, map[string]string{"status": "not_ready"})
		return
	}
	writeJSON(writer, http.StatusOK, map[string]string{"status": "ready"})
}

func (gateway *Gateway) proxyError(writer http.ResponseWriter, request *http.Request, err error) {
	writer.Header().Set(requestIDHeader, request.Header.Get(requestIDHeader))
	status := http.StatusBadGateway
	if errors.Is(request.Context().Err(), context.DeadlineExceeded) {
		status = http.StatusGatewayTimeout
	}
	writeJSON(writer, status, map[string]string{"detail": http.StatusText(status)})
}

func isPublicAPI(path string) bool {
	return path == "/api/auth/register" || path == "/api/auth/login"
}

func (gateway *Gateway) enforceRateLimit(writer http.ResponseWriter, request *http.Request, requestID, userID string) bool {
	identity, policy, limit := gateway.rateLimitPolicy(request, userID)
	result, err := gateway.limiter.Allow(request.Context(), identity, limit, gateway.config.RateLimitWindow)
	if err != nil {
		gateway.logger.Warn(
			"rate_limit_fail_open",
			"request_id", requestID,
			"policy", policy,
			"error", err,
		)
		return false
	}
	writer.Header().Set("X-RateLimit-Limit", fmt.Sprint(limit))
	writer.Header().Set("X-RateLimit-Remaining", fmt.Sprint(result.Remaining))
	if result.Allowed {
		return false
	}
	retryAfterSeconds := int(math.Ceil(result.RetryAfter.Seconds()))
	if retryAfterSeconds < 1 {
		retryAfterSeconds = 1
	}
	writer.Header().Set("Retry-After", fmt.Sprint(retryAfterSeconds))
	writeJSON(writer, http.StatusTooManyRequests, map[string]string{"detail": "rate limit exceeded"})
	return true
}

func (gateway *Gateway) rateLimitPolicy(request *http.Request, userID string) (identity, policy string, limit int) {
	if isPublicAPI(request.URL.Path) {
		return "ip:" + hashIdentity(clientIP(request.RemoteAddr)) + ":auth", "auth_ip", gateway.config.RateLimitAuth
	}
	if request.URL.Path == "/api/chat" {
		return "user:" + userID + ":chat", "chat_user", gateway.config.RateLimitChat
	}
	return "user:" + userID + ":api", "api_user", gateway.config.RateLimitUser
}

func clientIP(remoteAddress string) string {
	host, _, err := net.SplitHostPort(remoteAddress)
	if err == nil && host != "" {
		return host
	}
	if remoteAddress == "" {
		return "unknown"
	}
	return remoteAddress
}

func hashIdentity(value string) string {
	digest := sha256.Sum256([]byte(value))
	return hex.EncodeToString(digest[:16])
}

func newRequestID() string {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err == nil {
		return hex.EncodeToString(value)
	}
	return hex.EncodeToString([]byte(time.Now().UTC().Format(time.RFC3339Nano)))
}

func writeJSON(writer http.ResponseWriter, status int, value any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(value)
}

type statusRecorder struct {
	http.ResponseWriter
	status      int
	bytes       int
	wroteHeader bool
}

func (recorder *statusRecorder) WriteHeader(status int) {
	if recorder.wroteHeader {
		return
	}
	recorder.wroteHeader = true
	recorder.status = status
	recorder.ResponseWriter.WriteHeader(status)
}

func (recorder *statusRecorder) Write(value []byte) (int, error) {
	if !recorder.wroteHeader {
		recorder.WriteHeader(http.StatusOK)
	}
	written, err := recorder.ResponseWriter.Write(value)
	recorder.bytes += written
	return written, err
}

func (recorder *statusRecorder) Unwrap() http.ResponseWriter {
	return recorder.ResponseWriter
}
