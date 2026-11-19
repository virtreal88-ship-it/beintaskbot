package main

import (
	"net/http/httptest"
	"strings"
	"testing"
	"wacalls/internal/unified"
)

func TestAvatarScopeAndChatRejectedBeforeProvider(t *testing.T) {
	scope := unified.Scope{Tenant: "t", Source: "s", Account: "994100000001@s.whatsapp.net", Generation: "g1"}
	s := &server{appGateway: &appGateway{cfg: appGatewayConfig{Messaging: &managedMessagingConfig{Scope: scope}}}}
	for _, body := range []string{
		`{"scope":{"tenant":"other"},"chat":"123456@g.us"}`,
		`{"scope":{"tenant":"t","source":"s","account":"994100000001@s.whatsapp.net","generation":"g1"},"chat":"status@broadcast"}`,
		`{"scope":{"tenant":"t","source":"s","account":"994100000001@s.whatsapp.net","generation":"g1"},"chat":"123456@g.us","url":"http://127.0.0.1"}`,
	} {
		w := httptest.NewRecorder()
		s.handleManagedAvatar(w, httptest.NewRequest("POST", "/internal/messages/avatar", strings.NewReader(body)))
		if w.Code != 400 {
			t.Fatal(w.Code)
		}
	}
}
func TestAvatarAllowsOnlyImageBytesAndExactJIDs(t *testing.T) {
	if avatarMIME([]byte("<svg onload='evil()'></svg>")) != "" {
		t.Fatal("SVG allowed")
	}
	if avatarMIME([]byte{0xff, 0xd8, 0xff, 0x00}) != "image/jpeg" {
		t.Fatal("JPEG rejected")
	}
	for _, jid := range []string{"994100000002@s.whatsapp.net", "123456789@lid", "123456789@g.us"} {
		if !avatarPeer.MatchString(jid) {
			t.Fatal("valid scoped peer rejected")
		}
	}
	for _, jid := range []string{"status@broadcast", "123456:2@s.whatsapp.net", "http://127.0.0.1", "123456@g.us/other"} {
		if avatarPeer.MatchString(jid) {
			t.Fatal("noncanonical peer accepted")
		}
	}
}
