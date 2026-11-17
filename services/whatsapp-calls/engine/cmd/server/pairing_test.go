package main

import (
	"encoding/json"
	"strings"
	"testing"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store"
)

func TestPairingFailureClearsQRInReconnectSnapshot(t *testing.T) {
	for _, event := range []string{"err-client-outdated", "error", "err-unexpected-state", "err-scanned-without-multidevice"} {
		t.Run(event, func(t *testing.T) {
			mgr := &SessionManager{broker: NewBroker(), sessions: map[string]*Session{}}
			s := &Session{id: "test-session", mgr: mgr,
				client: &whatsmeow.Client{Store: &store.Device{}},
				auth:   AuthSnapshot{State: "qr", QR: "synthetic-pairing-code"}}
			mgr.register(s)
			before, err := json.Marshal(mgr.snapshotEvents())
			if err != nil || !strings.Contains(string(before), "synthetic-pairing-code") {
				t.Fatal("reconnecting browser must receive the current QR")
			}
			state := pairingFailureState(event)
			if state == "" || state == "connecting" || state == "qr" {
				t.Fatal("terminal pairing event must leave waiting state")
			}
			s.setAuth(AuthSnapshot{State: state})
			after, err := json.Marshal(mgr.snapshotEvents())
			if err != nil || strings.Contains(string(after), "synthetic-pairing-code") || !strings.Contains(string(after), state) {
				t.Fatal("failure must expose safe state and discard stale QR")
			}
			if s.info().Paired {
				t.Fatal("pairing failure cannot mark a device paired")
			}
		})
	}
}

func TestPairingFailureIgnoresNonFailures(t *testing.T) {
	for _, event := range []string{"code", "success", "timeout", "unrecognized"} {
		if pairingFailureState(event) != "" {
			t.Fatalf("unexpected failure for %s", event)
		}
	}
}
