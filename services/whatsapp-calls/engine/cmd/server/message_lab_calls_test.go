//go:build unifiedlab

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"wacalls/internal/recording"
)

func TestLabCallScopeIsExplicitAndSinglePeer(t *testing.T) {
	m, _, _ := labFixture(t)
	input := labCallRequest{Operation: randomUUID(), Session: m.cfg.SessionID, Peer: m.cfg.AllowedChats[0], SDP: "v=0\r\n", Consent: true}
	if m.validateCall(input) {
		t.Fatal("calls enabled without opt in")
	}
	m.cfg.CallTests = true
	if !m.validateCall(input) {
		t.Fatal("configured test denied")
	}
	for _, change := range []func(*labCallRequest){
		func(r *labCallRequest) { r.Session = "another-session" },
		func(r *labCallRequest) { r.Peer = "15550000003@s.whatsapp.net" },
		func(r *labCallRequest) { r.Peer = "123456@lid" },
		func(r *labCallRequest) { r.Peer = "123456@g.us" },
		func(r *labCallRequest) { r.Consent = false },
		func(r *labCallRequest) { r.Operation = "unbounded-key" },
		func(r *labCallRequest) { r.SDP = strings.Repeat("v=0", 8001) },
	} {
		bad := input
		change(&bad)
		if m.validateCall(bad) {
			t.Fatal("unscoped call admitted")
		}
	}
	m.cfg.AllowedChats = append(m.cfg.AllowedChats, "15550000003@s.whatsapp.net")
	if m.validateCall(input) {
		t.Fatal("ambiguous peer admitted")
	}
}

func TestLabCallProfileCannotExtendManagedOrMismatchedAccount(t *testing.T) {
	g, sess := testGateway(t)
	m, _, _ := labFixture(t)
	m.cfg.CallTests = true
	m.cfg.Scope.Account = g.cfg.Account
	m.cfg.SessionID = sess.id
	m.cfg.AllowedChats = []string{"15550000002@s.whatsapp.net"}
	g.server.recordings.cfg.Peers = m.cfg.AllowedChats
	if err := m.prepareCallTests(g.server); err != nil {
		t.Fatal(err)
	}
	if m.calls == nil || g.server.recordings.maxSeconds() != recording.ExtendedTestMaxSeconds {
		t.Fatal("bounded test profile unavailable")
	}
	g.server.recordings.cfg.Managed = true
	if err := m.prepareCallTests(g.server); err == nil {
		t.Fatal("managed source admitted")
	}
	if liveCallMaxSeconds(g.server.recordings) != 3600 {
		t.Fatal("managed live calls should allow one hour")
	}
	if g.server.recordings.maxSeconds() != recording.ManagedMaxSeconds {
		t.Fatal("managed bound changed")
	}
	g.server.recordings.cfg.Managed = false
	m.cfg.Scope.Account = "15550000009@s.whatsapp.net"
	if err := m.prepareCallTests(g.server); err == nil {
		t.Fatal("other account admitted")
	}
	if liveCallMaxSeconds(nil) != recording.MaxSeconds {
		t.Fatal("legacy bound changed")
	}
}

func TestLabCallRouteRemainsGatedAndGenericDialDenied(t *testing.T) {
	m, s, _ := labFixture(t)
	token := strings.Repeat("x", 40)
	guard, _ := newLabGuard("http://127.0.0.1:8080", token)
	handler := guard.wrap(s.routes())
	for _, test := range []struct {
		path string
		code int
	}{
		{"/api/unified-lab/calls", 404},
		{"/api/sessions/other/calls", 403},
	} {
		r := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8080"+test.path, strings.NewReader(`{}`))
		r.SetBasicAuth("lab", token)
		r.Header.Set("Origin", "http://127.0.0.1:8080")
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != test.code {
			t.Fatalf("%s: %d", test.path, w.Code)
		}
	}
	m.cfg.CallTests = true
	m.calls = &appGateway{}
	r := httptest.NewRequest("POST", "http://127.0.0.1:8080/api/unified-lab/calls", strings.NewReader(`{"owner":"another"}`))
	r.SetBasicAuth("lab", token)
	r.Header.Set("Origin", "http://127.0.0.1:8080")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)
	if w.Code != 403 {
		t.Fatal("browser supplied identity accepted")
	}
}

func TestLabCommandJournalBindsServerSelectedOwner(t *testing.T) {
	g, s := testGateway(t)
	cmd := cancelledCommand()
	first := g.executeOwned(context.Background(), s, cmd, "lab-owner")
	if first.Error != "call_ended" || !first.NotStarted {
		t.Fatal(first)
	}
	replay := g.executeOwned(context.Background(), s, cmd, "lab-owner")
	if replay != first {
		t.Fatal("same command not idempotent")
	}
	if g.execute(context.Background(), s, cmd).Error != "scope_denied" {
		t.Fatal("cross owner replay")
	}
}

func TestLabUnknownCommandBlocksConcurrentNewStartsAfterRestart(t *testing.T) {
	g, s := testGateway(t)
	if _, err := g.server.sessions.store.db.Exec("INSERT INTO app_commands(id,request,state) VALUES(?,?,'executing')", randomUUID(), "unknown"); err != nil {
		t.Fatal(err)
	}
	m := &messageLab{calls: g} // fresh in-memory state over retained journal
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r := m.executeCallTest(context.Background(), s, cancelledCommand())
			if r.Error != "previous_test_call_unconfirmed" || !r.NotStarted {
				t.Error(r)
			}
		}()
	}
	wg.Wait()
	var count int
	if err := g.server.sessions.store.db.QueryRow("SELECT count(*) FROM app_commands").Scan(&count); err != nil || count != 1 || s.reg.count() != 0 {
		t.Fatal("unknown start bypassed")
	}
}
