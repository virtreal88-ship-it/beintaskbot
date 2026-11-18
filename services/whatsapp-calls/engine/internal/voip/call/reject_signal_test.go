package call

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"

	waBinary "go.mau.fi/whatsmeow/binary"
	"go.mau.fi/whatsmeow/types"
	"wacalls/internal/voip/core"
)

type rejectSocket struct {
	core.VoipSocket
	reject func(context.Context, types.JID, types.JID, string) error
}

func (s rejectSocket) RejectIncomingCall(ctx context.Context, peer, creator types.JID, id string) error {
	return s.reject(ctx, peer, creator, id)
}

func rejectManager(sock core.VoipSocket) *CallManager {
	m := NewCallManager(sock, nil)
	m.currentCall = NewIncomingCall("test-call", "123456789:4@lid", "123456789@lid", "", core.CallMediaTypeAudio)
	return m
}

func TestRejectCompletesBoundedSignalBeforeEndingDespiteHTTPRequestCancellation(t *testing.T) {
	started, finish := make(chan struct{}), make(chan struct{})
	var sends, ended atomic.Int32
	sock := rejectSocket{reject: func(ctx context.Context, peer, creator types.JID, id string) error {
		sends.Add(1)
		if ctx.Err() != nil || peer.String() != "123456789:4@lid" || creator.String() != "123456789@lid" || id != "test-call" {
			t.Error("wrong/cancelled reject context")
		}
		deadline, ok := ctx.Deadline()
		if !ok || time.Until(deadline) > 8*time.Second {
			t.Error("unbounded reject")
		}
		close(started)
		<-finish
		return nil
	}}
	m := rejectManager(sock)
	m.OnEnded = func(*CallInfo) { ended.Add(1) }
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	result := make(chan error, 1)
	go func() { result <- m.RejectCall(ctx, "test-call", core.EndCallReasonDeclined) }()
	<-started
	if ended.Load() != 0 {
		t.Fatal("ended before provider acknowledgement")
	}
	if err := m.AcceptCall(context.Background(), "test-call"); err == nil {
		t.Fatal("accepted while rejection pending")
	}
	if err := m.RejectCall(context.Background(), "test-call", core.EndCallReasonDeclined); err == nil {
		t.Fatal("duplicate reject submitted")
	}
	close(finish)
	if err := <-result; err != nil {
		t.Fatal(err)
	}
	if sends.Load() != 1 || ended.Load() != 1 || !m.CurrentCall().IsEnded() {
		t.Fatal("terminal operation mismatch")
	}
}

func TestRejectFailureIsNotACompletedCall(t *testing.T) {
	m := rejectManager(rejectSocket{reject: func(context.Context, types.JID, types.JID, string) error { return errors.New("PRIVATE_UPSTREAM_ERROR") }})
	ended := false
	m.OnEnded = func(*CallInfo) { ended = true }
	var code string
	m.OnControl = func(op, value string) {
		if op != "reject" {
			t.Fatal("operation")
		}
		code = value
	}
	err := m.RejectCall(context.Background(), "test-call", core.EndCallReasonDeclined)
	if err == nil || err.Error() != "call_reject_unconfirmed" || ended || m.CurrentCall().IsEnded() || code != "transport_unconfirmed" {
		t.Fatal("failed signal falsely closed the call or exposed provider error")
	}
}

func TestRemoteTerminationDuringRejectDoesNotEndTwice(t *testing.T) {
	started, finish := make(chan struct{}), make(chan struct{})
	m := rejectManager(rejectSocket{reject: func(context.Context, types.JID, types.JID, string) error { close(started); <-finish; return nil }})
	var ended atomic.Int32
	m.OnEnded = func(*CallInfo) { ended.Add(1) }
	result := make(chan error, 1)
	go func() { result <- m.RejectCall(context.Background(), "test-call", core.EndCallReasonDeclined) }()
	<-started
	m.HandleCallTerminate(&waBinary.Node{Tag: "call", Content: []waBinary.Node{{Tag: "terminate", Attrs: waBinary.Attrs{"call-id": "test-call"}}}})
	close(finish)
	if err := <-result; err != nil || ended.Load() != 1 {
		t.Fatal("remote/local terminal race")
	}
}
