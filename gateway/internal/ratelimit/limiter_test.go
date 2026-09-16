package ratelimit

import (
	"context"
	"errors"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

type fakeClient struct {
	value  []any
	err    error
	key    string
	window any
}

func (client *fakeClient) Eval(_ context.Context, _ string, keys []string, args ...any) *redis.Cmd {
	client.key = keys[0]
	client.window = args[0]
	return redis.NewCmdResult(client.value, client.err)
}

func (client *fakeClient) Close() error { return nil }

func TestAllowReturnsRemainingAndRetryAfter(t *testing.T) {
	client := &fakeClient{value: []any{int64(2), int64(40_000)}}
	limiter, err := NewWithClient(client, "ciw:gateway:ratelimit:v1", 50*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	result, err := limiter.Allow(context.Background(), "user:42:chat", 2, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Allowed || result.Remaining != 0 || result.RetryAfter != 0 {
		t.Fatalf("result = %#v", result)
	}
	if client.key != "ciw:gateway:ratelimit:v1:user:42:chat" || client.window != int64(60_000) {
		t.Fatalf("key = %q, window = %#v", client.key, client.window)
	}

	client.value = []any{int64(3), int64(39_500)}
	result, err = limiter.Allow(context.Background(), "user:42:chat", 2, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	if result.Allowed || result.Remaining != 0 || result.RetryAfter != 39_500*time.Millisecond {
		t.Fatalf("blocked result = %#v", result)
	}
}

func TestAllowRejectsRedisAndProtocolErrors(t *testing.T) {
	client := &fakeClient{err: errors.New("redis unavailable")}
	limiter, err := NewWithClient(client, "prefix", time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := limiter.Allow(context.Background(), "identity", 1, time.Second); err == nil || !strings.Contains(err.Error(), "redis unavailable") {
		t.Fatalf("Allow() error = %v", err)
	}
	client.err = nil
	client.value = []any{int64(1)}
	if _, err := limiter.Allow(context.Background(), "identity", 1, time.Second); err == nil {
		t.Fatal("Allow() accepted malformed script response")
	}
}

func TestLimiterRejectsInvalidConfigurationAndInput(t *testing.T) {
	if _, err := New("not-a-url", "prefix", time.Second); err == nil {
		t.Fatal("New() accepted invalid Redis URL")
	}
	if _, err := NewWithClient(nil, "prefix", time.Second); err == nil {
		t.Fatal("NewWithClient() accepted nil client")
	}
	client := &fakeClient{}
	limiter, err := NewWithClient(client, "prefix", time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := limiter.Allow(context.Background(), "", 1, time.Second); err == nil {
		t.Fatal("Allow() accepted empty identity")
	}
}

func TestLimiterWithRealRedis(t *testing.T) {
	redisURL := os.Getenv("TEST_REDIS_URL")
	if redisURL == "" {
		t.Skip("TEST_REDIS_URL is not set")
	}
	prefix := "ciw:gateway:ratelimit:test:" + strconv.FormatInt(time.Now().UnixNano(), 10)
	limiter, err := New(redisURL, prefix, 500*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	defer limiter.Close()
	options, err := redis.ParseURL(redisURL)
	if err != nil {
		t.Fatal(err)
	}
	cleanupClient := redis.NewClient(options)
	defer cleanupClient.Close()
	key := prefix + ":user:42:chat"
	defer cleanupClient.Del(context.Background(), key)

	first, err := limiter.Allow(context.Background(), "user:42:chat", 1, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	second, err := limiter.Allow(context.Background(), "user:42:chat", 1, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	if !first.Allowed || second.Allowed || second.RetryAfter <= 0 {
		t.Fatalf("first = %#v, second = %#v", first, second)
	}
	ttl, err := cleanupClient.PTTL(context.Background(), key).Result()
	if err != nil || ttl <= 0 || ttl > time.Minute {
		t.Fatalf("PTTL = %s, error = %v", ttl, err)
	}
}
