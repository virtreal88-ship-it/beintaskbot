package main

import (
	"context"
	"github.com/coder/websocket"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
	"wacalls/internal/voip/core"
)

// A delayed browser must not accept the provider call and fill an undrained
// output queue while command/poll/permission round trips are still running.
func TestManagedProviderWaitsForBrowserAudioAndStartsOnce(t *testing.T) {
	var starts atomic.Int32
	bridge := newStreamBridge(nil, nil)
	bridge.stream.setStart(func() error { starts.Add(1); return nil })
	server := httptest.NewServer(http.HandlerFunc(bridge.stream.serve))
	defer server.Close()
	if starts.Load() != 0 {
		t.Fatal("started before browser")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	socket, _, err := websocket.Dial(ctx, server.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer socket.CloseNow()
	if starts.Load() != 0 {
		t.Fatal("upstream handshake is not browser readiness")
	}
	for i := 0; i < 5; i++ {
		if err = socket.Write(ctx, websocket.MessageBinary, []byte{0, 0, 0, 0}); err != nil {
			t.Fatal(err)
		}
	}
	deadline := time.Now().Add(time.Second)
	for starts.Load() == 0 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if starts.Load() != 1 {
		t.Fatal("provider start must execute once")
	}
	if err = bridge.WritePCM([]float32{.2, -.2}); err != nil {
		t.Fatal(err)
	}
	kind, raw, err := socket.Read(ctx)
	if err != nil || kind != websocket.MessageBinary || len(raw) != 4 {
		t.Fatal("audio unavailable after start")
	}
}

func TestCancelPreparedCallFencesLateProviderStart(t *testing.T) {
	g, sess := testGateway(t)
	g.cfg.Managed = true
	id := "prepared-synthetic"
	sess.createCall(id)
	owner := "app:synthetic"
	g.server.broker.upsertCall(CallRecord{SessionID: sess.id, CallID: id, Owner: &owner, Peer: g.cfg.Peers[0], Direction: "outbound", Status: StatusStarting})
	if _, err := g.makeBridge(sess, id, owner, ""); err != nil {
		t.Fatal(err)
	}
	ac, _ := sess.reg.get(id)
	bridge := ac.bridge.Load()
	var starts atomic.Int32
	g.armManaged(sess, id, func() error { starts.Add(1); return nil })
	sess.terminateCall(id, core.EndCallReasonUserEnded)
	bridge.stream.startProvider()
	if starts.Load() != 0 || sess.reg.count() != 0 {
		t.Fatal("cancelled preparation was started or left active")
	}
	if len(g.server.broker.historyRows(sess.id, 10)) != 1 {
		t.Fatal("confirmed unstarted closure missing")
	}
}

func TestClosedManagedBridgeCannotStartProvider(t *testing.T) {
	var starts atomic.Int32
	bridge := newStreamBridge(nil, nil)
	bridge.stream.setStart(func() error { starts.Add(1); return nil })
	bridge.Close()
	bridge.stream.startProvider()
	if starts.Load() != 0 {
		t.Fatal("late audio resurrected a closed call")
	}
}

func TestStopSerializesWithDeferredProviderStartup(t *testing.T) {
	g, sess := testGateway(t)
	g.cfg.Managed = true
	id := "starting-synthetic"
	sess.createCall(id)
	owner := "app:synthetic"
	g.server.broker.upsertCall(CallRecord{SessionID: sess.id, CallID: id, Owner: &owner, Peer: g.cfg.Peers[0], Direction: "outbound", Status: StatusStarting})
	g.makeBridge(sess, id, owner, "")
	ac, _ := sess.reg.get(id)
	bridge := ac.bridge.Load()
	started, finish, stopped := make(chan struct{}), make(chan struct{}), make(chan struct{})
	g.armManaged(sess, id, func() error { close(started); <-finish; return nil })
	go bridge.stream.startProvider()
	<-started
	go func() { sess.terminateCall(id, core.EndCallReasonUserEnded); close(stopped) }()
	select {
	case <-stopped:
		t.Fatal("stop overtook an admitted provider startup")
	case <-time.After(20 * time.Millisecond):
	}
	close(finish)
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("stop did not complete")
	}
	bridge.stream.startProvider()
	if sess.reg.count() != 0 {
		t.Fatal("prepared call retained after stop")
	}
}
