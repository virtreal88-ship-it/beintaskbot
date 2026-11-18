package main

import (
	"sync"
	"sync/atomic"
	"time"

	"wacalls/internal/voip/call"
)

type activeCall struct {
	id           string
	launchMu     sync.Mutex
	prepared     atomic.Bool
	recording    atomic.Pointer[recordingHandle]
	interrupted  atomic.Bool
	endOrigin    atomic.Pointer[string]
	rejectOrigin atomic.Pointer[string]
	cm           *call.CallManager
	bridge       atomic.Pointer[Bridge]
	setupMu      sync.Mutex
	timer        atomic.Pointer[time.Timer]
}

func (a *activeCall) markEnd(origin string) { a.endOrigin.CompareAndSwap(nil, &origin) }

func (r *callRegistry) attachRecording(id string, expected *activeCall, h *recordingHandle) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.calls[id] == expected && expected.recording.CompareAndSwap(nil, h)
}

type callRegistry struct {
	mu         sync.Mutex
	calls      map[string]*activeCall
	ended      map[string]struct{}
	endedOrder []string
}

func newCallRegistry() *callRegistry {
	return &callRegistry{calls: map[string]*activeCall{}, ended: map[string]struct{}{}}
}

func (r *callRegistry) add(callID string, ac *activeCall) bool { return r.addBounded(callID, ac, 0) }
func (r *callRegistry) addBounded(callID string, ac *activeCall, max int) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, ok := r.calls[callID]; ok {
		return false
	}
	if _, ok := r.ended[callID]; ok {
		return false
	}
	if max > 0 && len(r.calls) >= max {
		return false
	}
	r.calls[callID] = ac
	return true
}
func (r *callRegistry) rememberEnded(callID string) {
	r.ended[callID] = struct{}{}
	r.endedOrder = append(r.endedOrder, callID)
	if len(r.endedOrder) > 256 {
		delete(r.ended, r.endedOrder[0])
		r.endedOrder = r.endedOrder[1:]
	}
}
func (r *callRegistry) get(callID string) (*activeCall, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	ac, ok := r.calls[callID]
	return ac, ok
}

func (r *callRegistry) remove(callID string) (*activeCall, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	ac, ok := r.calls[callID]
	if !ok {
		return nil, false
	}
	delete(r.calls, callID)
	r.rememberEnded(callID)
	if timer := ac.timer.Swap(nil); timer != nil {
		timer.Stop()
	}
	return ac, true
}

func (r *callRegistry) count() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.calls)
}

func (r *callRegistry) setBridge(callID string, b *Bridge) (*Bridge, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	ac, ok := r.calls[callID]
	if !ok {
		return nil, false
	}
	oldB := ac.bridge.Swap(b)
	return oldB, true
}

func (r *callRegistry) drain() []*activeCall {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]*activeCall, 0, len(r.calls))
	for id, ac := range r.calls {
		r.rememberEnded(id)
		if timer := ac.timer.Swap(nil); timer != nil {
			timer.Stop()
		}
		out = append(out, ac)
	}
	r.calls = map[string]*activeCall{}
	return out
}
