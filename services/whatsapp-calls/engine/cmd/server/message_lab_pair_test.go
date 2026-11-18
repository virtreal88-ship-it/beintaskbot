//go:build unifiedlab

package main

import (
	"errors"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	waLog "go.mau.fi/whatsmeow/util/log"
)

func TestLabPairRepeatedAndConcurrentRequestsShareAttempt(t *testing.T) {
	m := &messageLab{}
	state := "absent"
	attempts := 0
	var wg sync.WaitGroup
	for n := 0; n < 20; n++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := m.pairOnce(func() string { return state }, func() error { attempts++; state = "pending"; return nil })
			if err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	if attempts != 1 {
		t.Fatalf("pair attempts: %d", attempts)
	}
	state = "paired"
	got, err := m.pairOnce(func() string { return state }, func() error { t.Fatal("paired session replaced"); return nil })
	if err != nil || got != "paired" {
		t.Fatal("paired session state changed")
	}
}

func TestLabPairFailedAttemptCanBeExplicitlyRetried(t *testing.T) {
	m := &messageLab{}
	if _, err := m.pairOnce(func() string { return "retry" }, func() error { return errors.New("test") }); err == nil {
		t.Fatal("failure masked")
	}
	if state, err := m.pairOnce(func() string { return "retry" }, func() error { return nil }); err != nil || state != "pending" {
		t.Fatal("explicit retry failed")
	}
}

func TestLabQRRejectsDifferentAccountBeforePairing(t *testing.T) {
	m, _, _ := labFixture(t)
	client := whatsmeow.NewClient(&store.Device{}, waLog.Noop)
	s := &Session{id: m.cfg.SessionID, client: client}
	if !m.attach(s) || client.PrePairCallback == nil {
		t.Fatal("pair guard missing")
	}
	wanted, _ := types.ParseJID(m.cfg.Scope.Account)
	wanted.Device = 27
	wrong := types.NewJID("15550000999", types.DefaultUserServer)
	wrong.Device = 27
	if !client.PrePairCallback(wanted, "", "") || client.PrePairCallback(wrong, "", "") {
		t.Fatal("wrong account admitted")
	}
}

func TestLabPairRoutesSharePinnedPendingSlotAndRequireAuth(t *testing.T) {
	m, s, _ := labFixture(t)
	s.sessions.sessions[m.cfg.SessionID] = &Session{id: m.cfg.SessionID, auth: AuthSnapshot{State: "qr"}}
	token := strings.Repeat("x", 40)
	guard, _ := newLabGuard("http://127.0.0.1:8080", token)
	handler := guard.wrap(s.routes())
	for _, path := range []string{"/api/unified-lab/pair", "/api/sessions/" + m.cfg.SessionID + "/pair"} {
		for _, auth := range []bool{false, true} {
			r := httptest.NewRequest("POST", "http://127.0.0.1:8080"+path, nil)
			if auth {
				r.SetBasicAuth("lab", token)
			}
			w := httptest.NewRecorder()
			handler.ServeHTTP(w, r)
			if !auth && w.Code != 401 {
				t.Fatal("unauthenticated pairing admitted")
			}
			if auth && (w.Code != 200 || !strings.Contains(w.Body.String(), `"state":"pending"`) || !strings.Contains(w.Body.String(), m.cfg.SessionID)) {
				t.Fatal("pending pinned slot not reused")
			}
		}
		r := httptest.NewRequest("POST", "http://127.0.0.1:8080"+path, nil)
		r.SetBasicAuth("lab", token)
		r.Header.Set("Origin", "https://other.invalid")
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != 403 {
			t.Fatal("cross origin pairing admitted")
		}
	}
}
