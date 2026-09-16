package config

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

type Config struct {
	Addr              string
	UpstreamURL       string
	JWTPublicKeyPath  string
	JWTIssuer         string
	JWTAudience       string
	UpstreamTimeout   time.Duration
	ReadinessTimeout  time.Duration
	ReadHeaderTimeout time.Duration
	ReadTimeout       time.Duration
	WriteTimeout      time.Duration
	IdleTimeout       time.Duration
	RateLimitEnabled  bool
	RateLimitRedisURL string
	RateLimitPrefix   string
	RateLimitWindow   time.Duration
	RateLimitTimeout  time.Duration
	RateLimitAuth     int
	RateLimitChat     int
	RateLimitUser     int
}

func Load() (Config, error) {
	config := Config{
		Addr:              env("GATEWAY_ADDR", ":8080"),
		UpstreamURL:       env("UPSTREAM_URL", "http://127.0.0.1:8000"),
		JWTPublicKeyPath:  env("JWT_PUBLIC_KEY_PATH", "../secrets/jwt-public.pem"),
		JWTIssuer:         env("JWT_ISSUER", "customer-intelligence-auth"),
		JWTAudience:       env("JWT_AUDIENCE", "customer-intelligence-api"),
		RateLimitRedisURL: env("GATEWAY_REDIS_URL", "redis://127.0.0.1:6379/0"),
		RateLimitPrefix:   env("RATE_LIMIT_KEY_PREFIX", "ciw:gateway:ratelimit:v1"),
	}
	var err error
	if config.RateLimitEnabled, err = boolEnv("RATE_LIMIT_ENABLED", true); err != nil {
		return Config{}, err
	}

	durations := []struct {
		name         string
		defaultValue time.Duration
		target       *time.Duration
	}{
		{"UPSTREAM_TIMEOUT", 30 * time.Second, &config.UpstreamTimeout},
		{"READINESS_TIMEOUT", 2 * time.Second, &config.ReadinessTimeout},
		{"READ_HEADER_TIMEOUT", 5 * time.Second, &config.ReadHeaderTimeout},
		{"READ_TIMEOUT", 30 * time.Second, &config.ReadTimeout},
		{"WRITE_TIMEOUT", 35 * time.Second, &config.WriteTimeout},
		{"IDLE_TIMEOUT", 60 * time.Second, &config.IdleTimeout},
		{"RATE_LIMIT_WINDOW", time.Minute, &config.RateLimitWindow},
		{"RATE_LIMIT_REDIS_TIMEOUT", 100 * time.Millisecond, &config.RateLimitTimeout},
	}
	for _, item := range durations {
		value, err := durationEnv(item.name, item.defaultValue)
		if err != nil {
			return Config{}, err
		}
		*item.target = value
	}
	if config.JWTIssuer == "" || config.JWTAudience == "" {
		return Config{}, fmt.Errorf("JWT issuer and audience must not be empty")
	}
	limits := []struct {
		name     string
		fallback int
		target   *int
	}{
		{"RATE_LIMIT_AUTH_REQUESTS", 10, &config.RateLimitAuth},
		{"RATE_LIMIT_CHAT_REQUESTS", 20, &config.RateLimitChat},
		{"RATE_LIMIT_USER_REQUESTS", 60, &config.RateLimitUser},
	}
	for _, item := range limits {
		if *item.target, err = positiveIntEnv(item.name, item.fallback); err != nil {
			return Config{}, err
		}
	}
	return config, nil
}

func env(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func durationEnv(name string, fallback time.Duration) (time.Duration, error) {
	raw := os.Getenv(name)
	if raw == "" {
		return fallback, nil
	}
	value, err := time.ParseDuration(raw)
	if err != nil || value <= 0 {
		return 0, fmt.Errorf("%s must be a positive duration", name)
	}
	return value, nil
}

func boolEnv(name string, fallback bool) (bool, error) {
	raw := os.Getenv(name)
	if raw == "" {
		return fallback, nil
	}
	value, err := strconv.ParseBool(raw)
	if err != nil {
		return false, fmt.Errorf("%s must be a boolean", name)
	}
	return value, nil
}

func positiveIntEnv(name string, fallback int) (int, error) {
	raw := os.Getenv(name)
	if raw == "" {
		return fallback, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil || value <= 0 {
		return 0, fmt.Errorf("%s must be a positive integer", name)
	}
	return value, nil
}
