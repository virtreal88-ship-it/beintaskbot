package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func testRecordingStore(t *testing.T) *recordingStore {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	dir := t.TempDir()
	db, e := openDB(filepath.Join(dir, "test.db"))
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { db.Close() })
	cfg := recordingConfig{Account: "123456@s.whatsapp.net", Peers: []string{"987654@lid"}, Seed: strings.Repeat("01", 32), Encryption: strings.Repeat("02", 32)}
	raw, _ := json.Marshal(cfg)
	p := filepath.Join(dir, "keys.json")
	if e = os.WriteFile(p, raw, 0600); e != nil {
		t.Fatal(e)
	}
	s, e := newRecordingStore(ctx, db, p)
	if e != nil {
		t.Fatal(e)
	}
	return s
}
func TestRecordingScopeEncryptionDuplicatesAndExpiry(t *testing.T) {
	s := testRecordingStore(t)
	for _, tc := range [][2]string{{"other@s.whatsapp.net", "987654@lid"}, {"123456@s.whatsapp.net", "other@lid"}} {
		if _, e := s.begin("s", "c", tc[0], tc[1]); e == nil {
			t.Fatal("unapproved call recorded")
		}
	}
	h, e := s.begin("s", "c", s.cfg.Account, s.cfg.Peers[0])
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.begin("s", "c", s.cfg.Account, s.cfg.Peers[0]); e == nil {
		t.Fatal("duplicate call capture")
	}
	h.capture.Add(0, []float32{.2, .3}, time.Now())
	h.capture.Add(1, []float32{.4, .5}, time.Now())
	h.finish(true)
	h.finish(false)
	var state string
	var encrypted []byte
	if e = s.db.QueryRow("SELECT state,encrypted FROM recordings WHERE id=?", h.id).Scan(&state, &encrypted); e != nil {
		t.Fatal(e)
	}
	if state != "ready" || bytes.Contains(encrypted, []byte("RIFF")) || bytes.Contains(encrypted, []byte("987654")) {
		t.Fatal("recording not encrypted or idempotent")
	}
	n := s.aead.NonceSize()
	plain, e := s.aead.Open(nil, encrypted[:n], encrypted[n:], []byte(h.id))
	if e != nil || !bytes.Contains(plain, []byte("RIFF")) {
		t.Fatal("encrypted recording unavailable")
	}
	if _, e = s.aead.Open(nil, encrypted[:n], encrypted[n:], []byte("another-recording")); e == nil {
		t.Fatal("recording identity not authenticated")
	}
	s.db.Exec("UPDATE recordings SET expires_at=0 WHERE id=?", h.id)
	if e = s.purge(context.Background()); e != nil {
		t.Fatal(e)
	}
	var count int
	s.db.QueryRow("SELECT count(*) FROM recordings").Scan(&count)
	if count != 0 {
		t.Fatal("expired audio retained")
	}
}
func TestRecordingDownloadRequiresMatchingSession(t *testing.T) {
	s := testRecordingStore(t)
	h, _ := s.begin("first", "c", s.cfg.Account, s.cfg.Peers[0])
	h.capture.Add(0, []float32{.2}, time.Now())
	h.finish(false)
	srv := &server{recordings: s, sessions: &SessionManager{sessions: map[string]*Session{"second": {id: "second"}}}}
	r := httptest.NewRequest("GET", "http://127.0.0.1:8080/api/sessions/second/recordings/"+h.id, nil)
	r.SetPathValue("sid", "second")
	r.SetPathValue("rid", h.id)
	w := httptest.NewRecorder()
	srv.handleRecordingDownload(w, r)
	if w.Code != 404 {
		t.Fatal("cross-session recording exposed")
	}
}
func TestLateRecordingCannotAttachToEndedCall(t *testing.T) {
	r := newCallRegistry()
	ac := &activeCall{}
	r.add("c", ac)
	r.remove("c")
	if r.attachRecording("c", ac, &recordingHandle{}) {
		t.Fatal("late capture attached after call ended")
	}
}

func TestFailedFinalWriteIsRetriedWithoutClaimingSuccess(t *testing.T) {
	s := testRecordingStore(t)
	h, e := s.begin("s", "retry", s.cfg.Account, s.cfg.Peers[0])
	if e != nil {
		t.Fatal(e)
	}
	h.capture.Add(0, []float32{.3}, time.Now())
	h.capture.Add(1, []float32{.5}, time.Now())
	// A temporary DB failure must retain only encrypted pending bytes and the
	// visible durable state must not claim that the recording is ready.
	_, e = s.db.Exec(`CREATE TRIGGER fail_final BEFORE UPDATE ON recordings BEGIN SELECT RAISE(FAIL,'test'); END`)
	if e != nil {
		t.Fatal(e)
	}
	h.finish(true)
	var state string
	s.db.QueryRow("SELECT state FROM recordings WHERE id=?", h.id).Scan(&state)
	if state != "recording" || len(s.pending) != 1 {
		t.Fatal("failed write hidden")
	}
	for _, p := range s.pending {
		if bytes.Contains(p.encrypted, []byte("RIFF")) {
			t.Fatal("plaintext pending")
		}
	}
	s.db.Exec("DROP TRIGGER fail_final")
	s.flushPending()
	s.flushPending()
	s.db.QueryRow("SELECT state FROM recordings WHERE id=?", h.id).Scan(&state)
	if state != "ready" || len(s.pending) != 0 {
		t.Fatal("finalization did not recover")
	}
}
