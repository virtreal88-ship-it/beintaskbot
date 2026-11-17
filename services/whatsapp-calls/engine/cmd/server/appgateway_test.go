package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"go.mau.fi/whatsmeow/types"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
	"wacalls/internal/voip/call"
	"wacalls/internal/voip/core"
)

func testGateway(t *testing.T) (*appGateway, *Session) {
	t.Helper()
	m := newTestManager(t)
	m.maxCalls = 1
	sess := m.addUnconnected(t, "synthetic")
	store := testRecordingStore(t)
	jid, _ := types.ParseJID(store.cfg.Account)
	sess.client.Store.ID = &jid
	srv := &server{sessions: m, broker: m.broker, recordings: store}
	c := appGatewayConfig{Endpoint: "https://example.invalid/api/platform/call-gateway", Token: strings.Repeat("x", 40), Session: sess.id, Account: store.cfg.Account, Peers: store.cfg.Peers}
	raw, _ := json.Marshal(c)
	path := filepath.Join(t.TempDir(), "gateway.json")
	os.WriteFile(path, raw, 0600)
	g, e := newAppGateway(srv, path)
	if e != nil {
		t.Fatal(e)
	}
	return g, sess
}
func cancelledCommand() appCommand {
	return appCommand{ID: randomUUID(), CallID: randomUUID(), Op: "start", Peer: "987654@lid", Cancelled: true}
}
func TestAppCommandConcurrentReplayAndScope(t *testing.T) {
	g, s := testGateway(t)
	cmd := cancelledCommand()
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r := g.execute(context.Background(), s, cmd)
			if !r.NotStarted || r.Error != "call_ended" {
				t.Error("cancelled replay executed")
			}
		}()
	}
	wg.Wait()
	var n int
	g.server.sessions.store.db.QueryRow("SELECT count(*) FROM app_commands").Scan(&n)
	if n != 1 || s.reg.count() != 0 {
		t.Fatal("duplicate execution")
	}
	cmd.Peer = "other@lid"
	if g.execute(context.Background(), s, cmd).Error != "scope_denied" {
		t.Fatal("wrong peer accepted")
	}
}
func TestAppUncertainJournalNeverRedials(t *testing.T) {
	g, s := testGateway(t)
	cmd := cancelledCommand()
	canonical := cmd
	canonical.Cancelled = false
	raw, _ := json.Marshal(canonical)
	sum := sha256.Sum256(raw)
	g.server.sessions.store.db.Exec("INSERT INTO app_commands(id,request,state) VALUES(?,?,'executing')", cmd.ID, hex.EncodeToString(sum[:]))
	r := g.execute(context.Background(), s, cmd)
	if r.Error != "command_uncertain" || r.NotStarted || s.reg.count() != 0 {
		t.Fatal("unknown command was replayed")
	}
}
func TestLateCallStateCannotResurrectEndedRegistry(t *testing.T) {
	g, s := testGateway(t)
	cm := s.createCall("synthetic-call")
	if cm == nil {
		t.Fatal("create")
	}
	info := &call.CallInfo{CallID: "synthetic-call", PeerJid: g.cfg.Peers[0]}
	cm.OnIncoming(info)
	s.removeCall(info.CallID)
	g.server.broker.endCall(info.CallID, "test")
	cm.OnIncoming(info)
	cm.OnStateChange(info)
	if _, ok := g.server.broker.getCall(info.CallID); ok {
		t.Fatal("late event revived call")
	}
	if s.reg.count() != 0 {
		t.Fatal("registry retained call")
	}
	_ = core.EndCallReasonUserEnded
}

func TestGatewayIdleSyncAlwaysUsesResultArray(t *testing.T) {
	g, _ := testGateway(t)
	seen := make(chan bool, 4)
	remote := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Error(err)
		}
		_, array := payload["results"].([]any)
		seen <- array
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"result":{"commands":[],"recordings":[]}}`))
	}))
	defer remote.Close()
	g.cfg.Endpoint = remote.URL
	g.client = remote.Client()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() { g.run(ctx); close(done) }()
	for n := 0; n < 3; n++ {
		select {
		case array := <-seen:
			if !array {
				t.Error("idle sync encoded results as null instead of an array")
			}
		case <-time.After(5 * time.Second):
			t.Fatal("gateway did not sync")
		}
	}
	cancel()
	<-done
}
