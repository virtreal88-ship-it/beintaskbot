package main

import (
	"context"
	"errors"
	"go.mau.fi/whatsmeow/types/events"
	"sync/atomic"
	"testing"
	"time"
)

func awaitCatchup(t *testing.T, c <-chan struct{}) {
	t.Helper()
	select {
	case <-c:
	case <-time.After(time.Second):
		t.Fatal("catchup stalled")
	}
}
func TestOfflineCatchupStopsAndRestartsWithoutDuplicatePump(t *testing.T) {
	sent := make(chan struct{}, 5)
	waiting := make(chan struct{}, 5)
	p := &offlineCatchup{send: func(context.Context) error { sent <- struct{}{}; return nil }, record: func(string) {}, wait: func(ctx context.Context) bool { waiting <- struct{}{}; <-ctx.Done(); return false }}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	p.observe(ctx, &events.OfflineSyncPreview{}, true)
	awaitCatchup(t, sent)
	awaitCatchup(t, waiting)
	for i := 0; i < 10; i++ {
		p.observe(ctx, &events.OfflineSyncPreview{}, true)
	}
	p.observe(ctx, &events.OfflineSyncCompleted{}, true)
	p.observe(ctx, &events.OfflineSyncPreview{}, true)
	p.mu.Lock()
	if !p.started || p.cancel != nil {
		t.Fatal("complete did not latch stop")
	}
	p.mu.Unlock()
	if len(sent) != 0 {
		t.Fatal("duplicate preview sent another request")
	}
	p.observe(ctx, &events.Disconnected{}, true)
	p.observe(ctx, &events.OfflineSyncPreview{}, true)
	awaitCatchup(t, sent)
	awaitCatchup(t, waiting)
	p.observe(ctx, &events.Message{}, false)
	p.mu.Lock()
	if !p.started || p.cancel != nil {
		t.Fatal("failed capture did not stop pump")
	}
	p.mu.Unlock()
}
func TestOfflineCatchupBoundAndWriteFailure(t *testing.T) {
	for _, failure := range []bool{false, true} {
		var sends atomic.Int32
		done := make(chan struct{}, 1)
		p := &offlineCatchup{send: func(context.Context) error {
			sends.Add(1)
			if failure {
				return errors.New("private provider detail")
			}
			return nil
		}, wait: func(context.Context) bool { return true }, record: func(code string) {
			if code == "offline_batch_limit_reached" || code == "offline_batch_write_failed" {
				done <- struct{}{}
			}
		}}
		p.preview(context.Background())
		awaitCatchup(t, done)
		want := int32(50)
		if failure {
			want = 1
		}
		if sends.Load() != want {
			t.Fatal(sends.Load())
		}
		p.preview(context.Background())
		p.stop(false)
	}
}
func TestOfflineCatchupOnlyExplicitSourceAndQueueRequest(t *testing.T) {
	t.Setenv("CALLS_NATIVE_OFFLINE_CATCHUP_SOURCES", "source-a, source-b")
	if !offlineCatchupEnabled("source-a") || !offlineCatchupEnabled("source-b") || offlineCatchupEnabled("source") || offlineCatchupEnabled("") {
		t.Fatal("scope gate")
	}
	n := offlineBatchNode()
	c := n.GetChildren()
	if n.Tag != "ib" || len(n.Attrs) != 0 || len(c) != 1 || c[0].Tag != "offline_batch" || c[0].Attrs["count"] != "100" || len(c[0].Attrs) != 1 || c[0].Content != nil {
		t.Fatal("unexpected queue request")
	}
}
func TestOfflineCatchupCancelledParentDoesNotSend(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	p := &offlineCatchup{send: func(context.Context) error { t.Error("sent after cancel"); return nil }, record: func(string) {}, wait: func(context.Context) bool { return true }}
	p.run(ctx)
}
