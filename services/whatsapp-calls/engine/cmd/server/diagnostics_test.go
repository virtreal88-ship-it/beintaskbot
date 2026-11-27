package main

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

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

func TestEndReasonIsAllowlistedAndOriginIsFirstWriter(t *testing.T) {
	if safeEndCode("private upstream data") != "unknown" || safeEndCode("user_ended") != "user_ended" {
		t.Fatal("unsafe provider end reason")
	}
	ac := &activeCall{}
	ac.markEnd("whatsapp_terminate")
	ac.markEnd("audio_transport")
	if *ac.endOrigin.Load() != "whatsapp_terminate" {
		t.Fatal("cleanup overwrote initiating event")
	}
}

func TestPerCallEvidenceSurvivesSubsequentCallsAndKeepsDirections(t *testing.T) {
	first, second := &activeCall{}, &activeCall{}
	start := time.Unix(1000, 0)
	first.diagnostic.browser.add(start)
	first.diagnostic.browser.add(start.Add(20 * time.Millisecond))
	first.diagnostic.whatsapp.add(start)
	first.diagnostic.whatsapp.add(start.Add(700 * time.Millisecond))
	first.diagnostic.end.Store(&callEndDiagnostic{"whatsapp_terminate", "user_ended", start.Add(time.Second).UnixMilli()})
	first.diagnostic.stream.Store(&streamEndDiagnostic{"call_closed", 2, 2, start.UnixMilli()})
	broker := NewBroker()
	broker.upsertCall(CallRecord{CallID: "one", SessionID: "session", Peer: "peer", Direction: "inbound"})
	broker.setCallDiagnostic("one", first.audioDiagnostic())
	broker.endCall("one", "user_ended")
	broker.upsertCall(CallRecord{CallID: "two", SessionID: "session", Peer: "peer", Direction: "inbound"})
	second.markEnd("audio_transport")
	broker.endCall("two", "user_ended")
	broker.setCallDiagnostic("two", second.audioDiagnostic())
	rows := broker.historyRows("session", 20)
	if len(rows) != 2 {
		t.Fatal("missing history")
	}
	byID := map[string]*perCallDiagnostic{}
	for _, row := range rows {
		byID[row.CallID] = row.Diagnostic
	}
	one, two := byID["one"], byID["two"]
	if one.End.Origin != "whatsapp_terminate" || two.End.Origin != "audio_transport" {
		t.Fatal("later call replaced original cause")
	}
	if one.WhatsApp.Gaps != 1 || one.WhatsApp.MaxGapMS != 700 || one.Browser.Gaps != 0 {
		t.Fatal("audio leg evidence mixed")
	}
	first.diagnostic.browser.add(start.Add(2 * time.Second))
	if one.Browser.Frames != 2 {
		t.Fatal("published snapshot was mutable")
	}
}
