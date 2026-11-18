package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	waBinary "go.mau.fi/whatsmeow/binary"
	"go.mau.fi/whatsmeow/types"
)

func TestHTTPRejectDoesNotRemoveCallOnTransportFailure(t *testing.T) {
	mgr := newTestManager(t)
	sess := mgr.addUnconnected(t, "synthetic")
	cm := sess.createCall("test-call")
	if cm == nil {
		t.Fatal("missing call")
	}
	defer sess.removeCall("test-call")
	peer := types.NewJID("123456789", types.HiddenUserServer)
	cm.HandleCallOffer(context.Background(), &waBinary.Node{Tag: "call", Content: []waBinary.Node{{Tag: "offer", Attrs: waBinary.Attrs{"call-id": "test-call", "call-creator": peer}}}}, peer)
	s := &server{sessions: mgr, broker: mgr.broker}
	r := httptest.NewRequest(http.MethodPost, "http://localhost/reject", nil)
	r.SetPathValue("id", "test-call")
	w := httptest.NewRecorder()
	s.doReject(sess, w, r)
	if w.Code != http.StatusServiceUnavailable || sess.reg.count() != 1 || cm.CurrentCall().IsEnded() {
		t.Fatal("unconnected reject falsely acknowledged")
	}
	if d := sess.diag.lastControl.Load(); d == nil || d.Code != "transport_unconfirmed" {
		t.Fatal("missing safe diagnosis")
	}
	ac, _ := sess.reg.get("test-call")
	if ac.endOrigin.Load() != nil {
		t.Fatal("failed reject incorrectly owns a later remote termination")
	}
}
