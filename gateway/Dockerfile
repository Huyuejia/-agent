FROM golang:1.27-alpine AS build

WORKDIR /src
COPY gateway/go.mod gateway/go.sum ./
RUN go mod download
COPY gateway/cmd ./cmd
COPY gateway/internal ./internal
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/gateway ./cmd/gateway

FROM alpine:3.22
RUN apk add --no-cache ca-certificates
COPY --from=build /out/gateway /usr/local/bin/gateway
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/gateway"]
