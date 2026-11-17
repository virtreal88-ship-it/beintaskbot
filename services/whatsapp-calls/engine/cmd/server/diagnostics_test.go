package main

import (
	"encoding/json"
	"strings"
	"testing"

	"go.mau.fi/whatsmeow/types/events"
	"wacalls/internal/recording"
)

func TestPrivateDiagnosticsDoNotTrustOldOpenState(t *testing.T) {
	m := newTestManager(t)
	s := m.addUnconnected(t, "test")
	s.setAuth(AuthSnapshot{State: "open", Paired: true})
	if s.transportReady() {
		t.Fatal("old open event advertised a disconnected transport")
	}
	s.handleEvent(&events.Disconnected{})
	s.handleEvent(&events.CallOfferNotice{})
	s.handleEvent(&events.UnknownCallEvent{})
	if s.info().State != "disconnected" {
		t.Fatal("disconnect did not invalidate event state")
	}
	d := s.diagnostics()
	if d["transport_connected"] != false || d["logged_in"] != false || d["active_registry_calls"] != 0 {
		t.Fatal(d)
	}
	if d["offer_events"] != uint64(0) || d["offer_notice_events"] != uint64(1) || d["unknown_call_events"] != uint64(1) {
		t.Fatal("notice must not be confused with an actionable offer", d)
	}
}

func TestRecordingDiagnosticsNeverExposeIdentifiersOrAudio(t *testing.T) {
	m := recording.Manifest{SessionID: "private-session", CallID: "private-call", AccountJID: "private-account", PeerJID: "private-peer", SHA256: "private-digest", Samples: [2]int{100, 200}, Gaps: [2]int{0, 2}, DurationMS: 1000, EndCode: "audio_gaps"}
	raw, err := json.Marshal(qualityOf(m))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "private") || strings.Contains(string(raw), "jid") || strings.Contains(string(raw), "sha256") {
		t.Fatal("sensitive metadata leaked")
	}
	if !strings.Contains(string(raw), `"arrival_gaps_over_200ms":[0,2]`) {
		t.Fatal("missing quality evidence")
	}
}
