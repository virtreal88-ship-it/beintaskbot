package main

import (
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
