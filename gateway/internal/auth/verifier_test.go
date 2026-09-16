package auth

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"testing"
	"time"
)

const (
	testIssuer   = "test-issuer"
	testAudience = "test-audience"
)

func TestVerifyAcceptsValidRS256Token(t *testing.T) {
	privateKey, verifier := testVerifier(t)
	now := time.Unix(1_800_000_000, 0)
	verifier.now = func() time.Time { return now }
	claims := validClaims(now)
	token := signToken(t, privateKey, map[string]any{"alg": "RS256", "typ": "JWT"}, claims)
	verified, err := verifier.VerifyBearer("bearer " + token)
	if err != nil {
		t.Fatalf("VerifyBearer() error = %v", err)
	}
	if verified.Subject != "42" {
		t.Fatalf("Subject = %q, want 42", verified.Subject)
	}
}

func TestVerifyAcceptsAudienceArray(t *testing.T) {
	privateKey, verifier := testVerifier(t)
	now := time.Unix(1_800_000_000, 0)
	verifier.now = func() time.Time { return now }
	claims := validClaims(now)
	claims["aud"] = []string{"another-service", testAudience}
	if _, err := verifier.Verify(signToken(t, privateKey, map[string]any{"alg": "RS256"}, claims)); err != nil {
		t.Fatalf("Verify() error = %v", err)
	}
}

func TestVerifyRejectsInvalidTokens(t *testing.T) {
	privateKey, verifier := testVerifier(t)
	otherKey, err := rsa.GenerateKey(rand.Reader, 1024)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(1_800_000_000, 0)
	verifier.now = func() time.Time { return now }
	tests := []struct {
		name   string
		header map[string]any
		claims map[string]any
		key    *rsa.PrivateKey
	}{
		{name: "wrong algorithm", header: map[string]any{"alg": "HS256"}, claims: validClaims(now), key: privateKey},
		{name: "wrong signature", header: map[string]any{"alg": "RS256"}, claims: validClaims(now), key: otherKey},
		{name: "expired", header: map[string]any{"alg": "RS256"}, claims: merge(validClaims(now), "exp", now.Unix()), key: privateKey},
		{name: "wrong issuer", header: map[string]any{"alg": "RS256"}, claims: merge(validClaims(now), "iss", "other"), key: privateKey},
		{name: "wrong audience", header: map[string]any{"alg": "RS256"}, claims: merge(validClaims(now), "aud", "other"), key: privateKey},
		{name: "invalid subject", header: map[string]any{"alg": "RS256"}, claims: merge(validClaims(now), "sub", "0"), key: privateKey},
		{name: "future issued at", header: map[string]any{"alg": "RS256"}, claims: merge(validClaims(now), "iat", now.Unix()+61), key: privateKey},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			token := signToken(t, test.key, test.header, test.claims)
			if _, err := verifier.Verify(token); !errors.Is(err, ErrInvalidToken) {
				t.Fatalf("Verify() error = %v, want ErrInvalidToken", err)
			}
		})
	}
}

func TestVerifyBearerRejectsMalformedHeader(t *testing.T) {
	_, verifier := testVerifier(t)
	for _, value := range []string{"", "Basic abc", "Bearer", "Bearer a b"} {
		if _, err := verifier.VerifyBearer(value); !errors.Is(err, ErrInvalidToken) {
			t.Fatalf("VerifyBearer(%q) error = %v, want ErrInvalidToken", value, err)
		}
	}
}

func TestNewVerifierRejectsInvalidKeyAndConfiguration(t *testing.T) {
	if _, err := NewVerifier([]byte("not pem"), testIssuer, testAudience); err == nil {
		t.Fatal("NewVerifier() accepted invalid PEM")
	}
	privateKey, err := rsa.GenerateKey(rand.Reader, 1024)
	if err != nil {
		t.Fatal(err)
	}
	publicDER, err := x509.MarshalPKIXPublicKey(&privateKey.PublicKey)
	if err != nil {
		t.Fatal(err)
	}
	publicPEM := pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: publicDER})
	if _, err := NewVerifier(publicPEM, "", testAudience); err == nil {
		t.Fatal("NewVerifier() accepted empty issuer")
	}
}

func testVerifier(t *testing.T) (*rsa.PrivateKey, *Verifier) {
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
	verifier, err := NewVerifier(publicPEM, testIssuer, testAudience)
	if err != nil {
		t.Fatal(err)
	}
	return privateKey, verifier
}

func validClaims(now time.Time) map[string]any {
	return map[string]any{
		"sub": "42", "iat": now.Unix(), "exp": now.Add(time.Minute).Unix(),
		"iss": testIssuer, "aud": testAudience,
	}
}

func merge(claims map[string]any, key string, value any) map[string]any {
	copy := make(map[string]any, len(claims))
	for name, existing := range claims {
		copy[name] = existing
	}
	copy[key] = value
	return copy
}

func signToken(t *testing.T, privateKey *rsa.PrivateKey, header, claims map[string]any) string {
	t.Helper()
	encode := func(value any) string {
		data, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		return base64.RawURLEncoding.EncodeToString(data)
	}
	unsigned := encode(header) + "." + encode(claims)
	digest := sha256.Sum256([]byte(unsigned))
	signature, err := rsa.SignPKCS1v15(rand.Reader, privateKey, crypto.SHA256, digest[:])
	if err != nil {
		t.Fatal(err)
	}
	return unsigned + "." + base64.RawURLEncoding.EncodeToString(signature)
}
