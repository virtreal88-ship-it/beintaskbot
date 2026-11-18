package main

import (
	"context"
	waBinary "go.mau.fi/whatsmeow/binary"
	"go.mau.fi/whatsmeow/types/events"
	"os"
	"strings"
	"sync"
	"time"
	"wacalls/internal/wa"
)

// Roll out only to explicitly selected existing sources; never creates a session.
func offlineCatchupEnabled(source string) bool {
	if source == "" {
		return false
	}
	for _, selected := range strings.Split(os.Getenv("CALLS_NATIVE_OFFLINE_CATCHUP_SOURCES"), ",") {
		if strings.TrimSpace(selected) == source {
			return true
		}
	}
	return false
}

// Only retrieves queued provider events. Existing encrypted capture and ACK rules
// remain authoritative. A successful write never means sync is complete.
func offlineBatchNode() waBinary.Node {
	return waBinary.Node{Tag: "ib", Content: []waBinary.Node{{Tag: "offline_batch", Attrs: waBinary.Attrs{"count": "100"}}}}
}

type offlineCatchup struct {
	mu      sync.Mutex
	started bool
	cancel  context.CancelFunc
	send    func(context.Context) error
	record  func(string)
	wait    func(context.Context) bool
}

func (p *offlineCatchup) preview(parent context.Context) {
	p.mu.Lock()
	if p.started {
		p.mu.Unlock()
		return
	}
	p.started = true
	ctx, cancel := context.WithCancel(parent)
	p.cancel = cancel
	p.mu.Unlock()
	go p.run(ctx)
}
func (p *offlineCatchup) stop(reset bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.cancel != nil {
		p.cancel()
		p.cancel = nil
	}
	p.started = !reset
}
func (p *offlineCatchup) run(ctx context.Context) {
	for n := 0; n < 50; n++ {
		if ctx.Err() != nil {
			return
		}
		writeCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
		err := p.send(writeCtx)
		cancel()
		if ctx.Err() != nil {
			return
		}
		if err != nil {
			p.record("offline_batch_write_failed")
			return
		}
		p.record("offline_batch_requested")
		if !p.wait(ctx) {
			return
		}
	}
	p.record("offline_batch_limit_reached")
}
func (g *appGateway) newOfflineCatchup(s *Session) *offlineCatchup {
	if g.cfg.Messaging == nil || !offlineCatchupEnabled(g.cfg.Messaging.Scope.Source) {
		return nil
	}
	client := s.client
	return &offlineCatchup{
		send:   func(ctx context.Context) error { return wa.NewSocket(client).SendNode(ctx, offlineBatchNode()) },
		record: g.providerDiagnostics.add,
		wait: func(ctx context.Context) bool {
			t := time.NewTimer(5 * time.Second)
			defer t.Stop()
			select {
			case <-ctx.Done():
				return false
			case <-t.C:
				return true
			}
		},
	}
}
func (p *offlineCatchup) observe(ctx context.Context, event any, stored bool) {
	if p == nil {
		return
	}
	if !stored {
		p.stop(false)
		return
	}
	switch event.(type) {
	case *events.OfflineSyncPreview:
		p.preview(ctx)
	case *events.OfflineSyncCompleted:
		p.stop(false)
	case *events.Disconnected:
		p.stop(true)
	case *events.LoggedOut, *events.StreamReplaced:
		p.stop(false)
	}
}
