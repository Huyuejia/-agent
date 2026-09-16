package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"customer-intelligence-workbench/gateway/internal/auth"
	"customer-intelligence-workbench/gateway/internal/config"
	"customer-intelligence-workbench/gateway/internal/ratelimit"
	"customer-intelligence-workbench/gateway/internal/server"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	configured, err := config.Load()
	if err != nil {
		logger.Error("invalid_configuration", "error", err)
		os.Exit(1)
	}
	verifier, err := auth.NewVerifierFromFile(
		configured.JWTPublicKeyPath,
		configured.JWTIssuer,
		configured.JWTAudience,
	)
	if err != nil {
		logger.Error("jwt_verifier_initialization_failed", "error", err)
		os.Exit(1)
	}
	var limiter *ratelimit.Limiter
	if configured.RateLimitEnabled {
		limiter, err = ratelimit.New(
			configured.RateLimitRedisURL,
			configured.RateLimitPrefix,
			configured.RateLimitTimeout,
		)
		if err != nil {
			logger.Error("rate_limiter_initialization_failed", "error", err)
			os.Exit(1)
		}
		defer func() {
			if err := limiter.Close(); err != nil {
				logger.Warn("rate_limiter_close_failed", "error", err)
			}
		}()
	}
	handler, err := server.New(configured, verifier, limiter, logger)
	if err != nil {
		logger.Error("gateway_initialization_failed", "error", err)
		os.Exit(1)
	}

	httpServer := &http.Server{
		Addr:              configured.Addr,
		Handler:           handler,
		ReadHeaderTimeout: configured.ReadHeaderTimeout,
		ReadTimeout:       configured.ReadTimeout,
		WriteTimeout:      configured.WriteTimeout,
		IdleTimeout:       configured.IdleTimeout,
	}

	shutdownSignals, stop := signal.NotifyContext(
		context.Background(),
		syscall.SIGINT,
		syscall.SIGTERM,
	)
	defer stop()
	go func() {
		<-shutdownSignals.Done()
		shutdownContext, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := httpServer.Shutdown(shutdownContext); err != nil {
			logger.Error("gateway_shutdown_failed", "error", err)
		}
	}()

	logger.Info("gateway_started", "addr", configured.Addr, "upstream", configured.UpstreamURL)
	if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		logger.Error("gateway_stopped_unexpectedly", "error", err)
		os.Exit(1)
	}
	logger.Info("gateway_stopped")
}
