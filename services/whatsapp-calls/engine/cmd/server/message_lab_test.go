//go:build unifiedlab

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
	"wacalls/internal/unified"
)

func labFixture(t *testing.T) (*messageLab, *server, func()) {
	t.Helper()
	db, err := openDB(filepath.Join(t.TempDir(), "lab.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	scope := unified.Scope{Tenant: "lab-tenant", Source: "lab-source", Account: "15550000001@s.whatsapp.net", Generation: "lab-1"}
	j, err := unified.New(context.Background(), db, scope, bytes.Repeat([]byte{5}, 32))
	if err != nil {
		t.Fatal(err)
	}
	m := &messageLab{cfg: messageLabSettings{Scope: scope, SessionID: "lab-session", AllowedChats: []string{"15550000002@s.whatsapp.net"}}, journal: j}
	s := &server{sessions: &SessionManager{messageLab: m, sessions: map[string]*Session{}}}
	return m, s, func() { db.Close() }
}

func TestLabCaptureDedupEditReceiptAndUnapprovedChat(t *testing.T) {
	m, _, closeDB := labFixture(t)
	ctx := context.Background()
	chat, _ := types.ParseJID(m.cfg.AllowedChats[0])
	event := &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: chat, Sender: chat}, ID: "provider-a", Timestamp: time.Unix(1000, 0)}, Message: &waE2E.Message{Conversation: proto.String("test text")}}
	if !m.capture(ctx, event) || !m.capture(ctx, event) {
		t.Fatal("capture failed")
	}
	copy := *event
	copy.Info.Chat, _ = types.ParseJID("15550000003@s.whatsapp.net")
	if !m.capture(ctx, &copy) {
		t.Fatal("unapproved event should be ignored without retaining content")
	}
	copy = *event
	copy.IsViewOnce = true
	if !m.capture(ctx, &copy) {
		t.Fatal("view once should be ignored")
	}
	copy = *event
	copy.IsEdit = true
	copy.Message = &waE2E.Message{Conversation: proto.String("edited test text")}
	if !m.capture(ctx, &copy) {
		t.Fatal("edit failed")
	}
	receipt := &events.Receipt{MessageSource: event.Info.MessageSource, MessageIDs: []string{"provider-a"}, Timestamp: time.Unix(1001, 0), Type: types.ReceiptTypeRead}
	if !m.capture(ctx, receipt) || !m.capture(ctx, receipt) {
		t.Fatal("receipt failed")
	}
	items, err := m.journal.Events(ctx, 0, 50)
	if err != nil || len(items) != 3 {
		t.Fatalf("expected original, edit, receipt; got %d: %v", len(items), err)
	}
	closeDB()
	if m.capture(ctx, event) {
		t.Fatal("DB failure must not acknowledge capture")
	}
}

func TestLabRoutesRequireAuthOriginAndRedactProtocol(t *testing.T) {
	m, s, _ := labFixture(t)
	ctx := context.Background()
	chat, _ := types.ParseJID(m.cfg.AllowedChats[0])
	event := &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: chat, Sender: chat}, ID: "provider-media"}, Message: &waE2E.Message{ImageMessage: &waE2E.ImageMessage{MediaKey: []byte("private-media-key")}}}
	if !m.capture(ctx, event) {
		t.Fatal("capture failed")
	}
	token := strings.Repeat("x", 40)
	guard, _ := newLabGuard("http://127.0.0.1:8080", token)
	handler := guard.wrap(s.routes())
	request := func(method, path, origin, body string, auth bool) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, "http://127.0.0.1:8080"+path, strings.NewReader(body))
		if origin != "" {
			r.Header.Set("Origin", origin)
		}
		if auth {
			r.SetBasicAuth("lab", token)
		}
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		return w
	}
	if w := request("GET", "/api/unified-lab/events", "", "", false); w.Code != 401 {
		t.Fatal("unauthenticated lab read")
	}
	if w := request("POST", "/api/unified-lab/text", "https://other.invalid", `{}`, true); w.Code != 403 {
		t.Fatal("cross origin admitted")
	}
	w := request("GET", "/api/unified-lab/events", "", "", true)
	if w.Code != 200 || strings.Contains(w.Body.String(), "proto") || strings.Contains(w.Body.String(), "media-key") {
		t.Fatal("protocol secret exposed by journal API")
	}
	var body map[string]any
	if json.Unmarshal(w.Body.Bytes(), &body) != nil {
		t.Fatal("invalid events JSON")
	}
	w = request("POST", "/api/unified-lab/text", "http://127.0.0.1:8080", `{"operation":"test","chat":"15550000003@s.whatsapp.net","text":"test"}`, true)
	if w.Code != 403 {
		t.Fatal("unapproved peer admitted")
	}
	w = request("POST", "/api/unified-lab/text", "http://127.0.0.1:8080", `{"operation":"test","chat":"15550000002@s.whatsapp.net","text":"test"}`, true)
	if w.Code != 409 {
		t.Fatal("missing provider session accepted")
	}
	if w := request("GET", "/unified-lab", "", "", true); w.Code != 200 || !strings.Contains(w.Header().Get("Content-Type"), "text/html") {
		t.Fatal("lab page unavailable")
	}
}

func TestLabDisabledWithoutRuntimeConfig(t *testing.T) {
	_, s, _ := labFixture(t)
	s.sessions.messageLab = nil
	mux := http.NewServeMux()
	s.registerMessageLab(mux)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, httptest.NewRequest("GET", "http://127.0.0.1/api/unified-lab/events", nil))
	if w.Code != 404 {
		t.Fatal("runtime-disabled lab API exposed")
	}
}
