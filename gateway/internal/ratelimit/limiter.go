package ratelimit

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

const fixedWindowScript = `
local current = redis.call("INCR", KEYS[1])
if current == 1 then
  redis.call("PEXPIRE", KEYS[1], ARGV[1])
end
local ttl = redis.call("PTTL", KEYS[1])
return {current, ttl}
`

type Result struct {
	Allowed    bool
	Remaining  int
	RetryAfter time.Duration
}

type Client interface {
	Eval(ctx context.Context, script string, keys []string, args ...any) *redis.Cmd
	Close() error
}

type Limiter struct {
	client           Client
	prefix           string
	operationTimeout time.Duration
}

func New(redisURL, prefix string, operationTimeout time.Duration) (*Limiter, error) {
	options, err := redis.ParseURL(redisURL)
	if err != nil {
		return nil, fmt.Errorf("parse gateway Redis URL: %w", err)
	}
	if prefix == "" || operationTimeout <= 0 {
		return nil, errors.New("rate-limit prefix and operation timeout must be valid")
	}
	return NewWithClient(redis.NewClient(options), prefix, operationTimeout)
}

func NewWithClient(client Client, prefix string, operationTimeout time.Duration) (*Limiter, error) {
	if client == nil || prefix == "" || operationTimeout <= 0 {
		return nil, errors.New("rate-limit client, prefix and operation timeout must be valid")
	}
	return &Limiter{client: client, prefix: prefix, operationTimeout: operationTimeout}, nil
}

func (limiter *Limiter) Allow(ctx context.Context, identity string, limit int, window time.Duration) (Result, error) {
	if identity == "" || limit <= 0 || window <= 0 {
		return Result{}, errors.New("rate-limit identity, limit and window must be valid")
	}
	operationContext, cancel := context.WithTimeout(ctx, limiter.operationTimeout)
	defer cancel()
	value, err := limiter.client.Eval(
		operationContext,
		fixedWindowScript,
		[]string{limiter.prefix + ":" + identity},
		window.Milliseconds(),
	).Slice()
	if err != nil {
		return Result{}, fmt.Errorf("evaluate rate-limit script: %w", err)
	}
	if len(value) != 2 {
		return Result{}, errors.New("invalid rate-limit script response")
	}
	current, currentOK := value[0].(int64)
	ttlMilliseconds, ttlOK := value[1].(int64)
	if !currentOK || !ttlOK || current <= 0 {
		return Result{}, errors.New("invalid rate-limit counters")
	}
	remaining := limit - int(current)
	if remaining < 0 {
		remaining = 0
	}
	retryAfter := time.Duration(0)
	if current > int64(limit) {
		if ttlMilliseconds <= 0 {
			ttlMilliseconds = window.Milliseconds()
		}
		retryAfter = time.Duration(ttlMilliseconds) * time.Millisecond
	}
	return Result{Allowed: current <= int64(limit), Remaining: remaining, RetryAfter: retryAfter}, nil
}

func (limiter *Limiter) Close() error {
	return limiter.client.Close()
}
