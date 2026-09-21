package config

import (
	"testing"
	"time"
)

func TestLoadReadsConfiguration(t *testing.T) {
	t.Setenv("GATEWAY_ADDR", ":9090")
	t.Setenv("UPSTREAM_URL", "http://backend:8000")
	t.Setenv("JWT_PUBLIC_KEY_PATH", "/keys/public.pem")
	t.Setenv("JWT_ISSUER", "issuer")
	t.Setenv("JWT_AUDIENCE", "audience")
	t.Setenv("UPSTREAM_TIMEOUT", "7s")
	t.Setenv("READINESS_TIMEOUT", "500ms")
	t.Setenv("READ_HEADER_TIMEOUT", "2s")
	t.Setenv("READ_TIMEOUT", "8s")
	t.Setenv("WRITE_TIMEOUT", "9s")
	t.Setenv("IDLE_TIMEOUT", "10s")
	configured, err := Load()
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}
	if configured.Addr != ":9090" || configured.UpstreamURL != "http://backend:8000" ||
		configured.JWTPublicKeyPath != "/keys/public.pem" || configured.JWTIssuer != "issuer" ||
		configured.JWTAudience != "audience" || configured.UpstreamTimeout != 7*time.Second ||
		configured.ReadinessTimeout != 500*time.Millisecond || configured.IdleTimeout != 10*time.Second {
		t.Fatalf("Load() = %#v", configured)
	}
}

func TestLoadRejectsInvalidDuration(t *testing.T) {
	t.Setenv("UPSTREAM_TIMEOUT", "0s")
	if _, err := Load(); err == nil {
		t.Fatal("Load() accepted a non-positive timeout")
	}
}

func TestLoadReadsRateLimitConfiguration(t *testing.T) {
	t.Setenv("RATE_LIMIT_ENABLED", "false")
	t.Setenv("GATEWAY_REDIS_URL", "redis://redis:6379/4")
	t.Setenv("RATE_LIMIT_KEY_PREFIX", "custom:limit")
	t.Setenv("RATE_LIMIT_WINDOW", "30s")
	t.Setenv("RATE_LIMIT_REDIS_TIMEOUT", "250ms")
	t.Setenv("RATE_LIMIT_AUTH_REQUESTS", "4")
	t.Setenv("RATE_LIMIT_CHAT_REQUESTS", "8")
	t.Setenv("RATE_LIMIT_USER_REQUESTS", "12")
	configured, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if configured.RateLimitEnabled || configured.RateLimitRedisURL != "redis://redis:6379/4" ||
		configured.RateLimitPrefix != "custom:limit" || configured.RateLimitWindow != 30*time.Second ||
		configured.RateLimitTimeout != 250*time.Millisecond || configured.RateLimitAuth != 4 ||
		configured.RateLimitChat != 8 || configured.RateLimitUser != 12 {
		t.Fatalf("Load() = %#v", configured)
	}
}

func TestLoadRejectsInvalidRateLimitConfiguration(t *testing.T) {
	t.Setenv("RATE_LIMIT_ENABLED", "sometimes")
	if _, err := Load(); err == nil {
		t.Fatal("Load() accepted invalid boolean")
	}
	t.Setenv("RATE_LIMIT_ENABLED", "true")
	t.Setenv("RATE_LIMIT_CHAT_REQUESTS", "0")
	if _, err := Load(); err == nil {
		t.Fatal("Load() accepted non-positive request limit")
	}
}
