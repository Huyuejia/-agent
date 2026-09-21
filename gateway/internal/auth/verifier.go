package auth

import (
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

var ErrInvalidToken = errors.New("invalid access token")

type Audience []string

func (audience *Audience) UnmarshalJSON(data []byte) error {
	var single string
	if err := json.Unmarshal(data, &single); err == nil {
		*audience = Audience{single}
		return nil
	}
	var multiple []string
	if err := json.Unmarshal(data, &multiple); err != nil {
		return ErrInvalidToken
	}
	*audience = multiple
	return nil
}

func (audience Audience) Contains(expected string) bool {
	for _, value := range audience {
		if value == expected {
			return true
		}
	}
	return false
}

type Claims struct {
	Subject   string   `json:"sub"`
	IssuedAt  int64    `json:"iat"`
	ExpiresAt int64    `json:"exp"`
	Issuer    string   `json:"iss"`
	Audience  Audience `json:"aud"`
}

type tokenHeader struct {
	Algorithm string `json:"alg"`
}

type Verifier struct {
	publicKey *rsa.PublicKey
	issuer    string
	audience  string
	now       func() time.Time
}

func NewVerifier(publicKeyPEM []byte, issuer, audience string) (*Verifier, error) {
	block, _ := pem.Decode(publicKeyPEM)
	if block == nil {
		return nil, fmt.Errorf("decode JWT public key: invalid PEM")
	}
	parsed, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse JWT public key: %w", err)
	}
	publicKey, ok := parsed.(*rsa.PublicKey)
	if !ok {
		return nil, fmt.Errorf("JWT public key must be RSA")
	}
	if issuer == "" || audience == "" {
		return nil, fmt.Errorf("JWT issuer and audience must not be empty")
	}
	return &Verifier{
		publicKey: publicKey,
		issuer:    issuer,
		audience:  audience,
		now:       time.Now,
	}, nil
}

func NewVerifierFromFile(path, issuer, audience string) (*Verifier, error) {
	value, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read JWT public key: %w", err)
	}
	return NewVerifier(value, issuer, audience)
}

func (verifier *Verifier) VerifyBearer(authorization string) (Claims, error) {
	parts := strings.Fields(authorization)
	if len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") {
		return Claims{}, ErrInvalidToken
	}
	return verifier.Verify(parts[1])
}

func (verifier *Verifier) Verify(token string) (Claims, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return Claims{}, ErrInvalidToken
	}
	headerBytes, err := decodeSegment(parts[0])
	if err != nil {
		return Claims{}, ErrInvalidToken
	}
	var header tokenHeader
	if json.Unmarshal(headerBytes, &header) != nil || header.Algorithm != "RS256" {
		return Claims{}, ErrInvalidToken
	}

	signature, err := decodeSegment(parts[2])
	if err != nil {
		return Claims{}, ErrInvalidToken
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if rsa.VerifyPKCS1v15(
		verifier.publicKey,
		crypto.SHA256,
		digest[:],
		signature,
	) != nil {
		return Claims{}, ErrInvalidToken
	}

	payload, err := decodeSegment(parts[1])
	if err != nil {
		return Claims{}, ErrInvalidToken
	}
	var claims Claims
	if json.Unmarshal(payload, &claims) != nil {
		return Claims{}, ErrInvalidToken
	}
	now := verifier.now().Unix()
	userID, subjectErr := strconv.ParseInt(claims.Subject, 10, 64)
	if subjectErr != nil || userID <= 0 || claims.IssuedAt <= 0 || claims.IssuedAt > now+60 ||
		claims.ExpiresAt <= now || claims.Issuer != verifier.issuer ||
		!claims.Audience.Contains(verifier.audience) {
		return Claims{}, ErrInvalidToken
	}
	return claims, nil
}

func decodeSegment(value string) ([]byte, error) {
	decoded, err := base64.RawURLEncoding.DecodeString(value)
	if err != nil {
		return nil, ErrInvalidToken
	}
	return decoded, nil
}
