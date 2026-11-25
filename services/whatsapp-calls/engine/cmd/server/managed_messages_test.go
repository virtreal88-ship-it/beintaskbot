package main

import (
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"wacalls/internal/unified"
)

func TestManagedMessagesDisabledByDefault(t *testing.T) {
	s := &server{}
	for _, path := range []string{"/internal/messages", "/internal/messages/media"} {
		r := httptest.NewRequest("POST", path, strings.NewReader(`{}`))
		w := httptest.NewRecorder()
		if strings.HasSuffix(path, "/media") {
			s.handleManagedMedia(w, r)
		} else {
			s.handleManagedMessage(w, r)
		}
		if w.Code != 409 {
			t.Fatal(w.Code)
		}
	}
}

func TestManagedMessageRejectsOtherScopeBeforeSessionAccess(t *testing.T) {
	s := &server{appGateway: &appGateway{messages: &unified.Journal{}, cfg: appGatewayConfig{Messaging: &managedMessagingConfig{Scope: unified.Scope{Tenant: "expected"}}}}}
	for _, path := range []string{"/internal/messages", "/internal/messages/media"} {
		r := httptest.NewRequest("POST", path, strings.NewReader(`{"scope":{"tenant":"other"}}`))
		w := httptest.NewRecorder()
		if strings.HasSuffix(path, "/media") {
			s.handleManagedMedia(w, r)
		} else {
			s.handleManagedMessage(w, r)
		}
		if w.Code != 400 {
			t.Fatal(w.Code)
		}
	}
}

type zeroVideoReader struct{}

func (zeroVideoReader) Read(p []byte) (int, error) {
	clear(p)
	return len(p), nil
}

func TestManagedBinaryVideoAtSizeLimit(t *testing.T) {
	scope := unified.Scope{Tenant: "test-tenant"}
	metadata, err := json.Marshal(unified.Outgoing{Scope: scope, Operation: "test-video", Kind: "video", MIME: "video/mp4", Chat: "15551234567@s.whatsapp.net"})
	if err != nil {
		t.Fatal(err)
	}
	command := base64.StdEncoding.EncodeToString(metadata)
	prefix := "\x00\x00\x00\x18ftypmp42"
	request := func(length int64) *http.Request {
		r := httptest.NewRequest("POST", "/internal/messages/video", io.MultiReader(strings.NewReader(prefix), zeroVideoReader{}))
		r.ContentLength = length
		r.Header.Set("Content-Type", "application/octet-stream")
		r.Header.Set("X-Native-Video-Command", command)
		return r
	}
	if _, err := decodeManagedOutgoing(request(unified.MaxVideoBytes+1), scope); err == nil {
		t.Fatal("over-limit video accepted")
	}
	if _, err := decodeManagedOutgoing(request(unified.MaxVideoBytes), unified.Scope{Tenant: "other"}); err == nil {
		t.Fatal("other scope accepted")
	}
	accepted, err := decodeManagedOutgoing(request(unified.MaxVideoBytes), scope)
	if err != nil || len(accepted.Data) != unified.MaxVideoBytes {
		t.Fatalf("100 MiB MP4 rejected: %v", err)
	}
	accepted.Kind, accepted.MIME = "image", "image/png"
	if err := accepted.Validate(scope); err != unified.ErrMediaSize {
		t.Fatalf("non-video limit changed: %v", err)
	}
}
