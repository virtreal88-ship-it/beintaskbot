package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestSharedRecordingCapacityAndExactAcknowledgement(t *testing.T) {
	s := testRecordingStore(t)
	s.cfg.Managed = true
	s.cfg.CapacityDir = t.TempDir()
	one, e := s.acquireWork(false)
	if e != nil {
		t.Fatal(e)
	}
	defer releaseWork(one)
	other := &recordingStore{cfg: s.cfg}
	two, e := other.acquireWork(false)
	if e != nil {
		t.Fatal(e)
	}
	if extra, e := s.acquireWork(false); e == nil {
		releaseWork(extra)
		t.Fatal("unbounded cross-process capacity")
	}
	releaseWork(two)
	h, e := s.begin("current", "provider", s.cfg.Account, s.cfg.Peers[0])
	if e != nil {
		t.Fatal(e)
	}
	h.capture.Add(0, []float32{.2}, time.Now())
	h.finish(false)
	g := &appGateway{cfg: appGatewayConfig{Managed: true, Session: "other"}, server: &server{recordings: s}}
	g.acknowledgeRecording("provider")
	g.cfg.Session = "current"
	if !g.canUpload("provider") {
		t.Fatal("another session acknowledgement discarded audio")
	}
	g.acknowledgeRecording("other-provider")
	if !g.canUpload("provider") {
		t.Fatal("another provider acknowledgement discarded audio")
	}
	g.acknowledgeRecording("provider")
	g.acknowledgeRecording("provider")
	if g.canUpload("provider") {
		t.Fatal("delivered raw spool retained")
	}
	var state string
	var size int
	if e = s.db.QueryRow("SELECT state,COALESCE(length(encrypted),0) FROM recordings WHERE id=?", h.id).Scan(&state, &size); e != nil || state != "delivered" || size != 0 {
		t.Fatal("wrong spool acknowledgement")
	}
}

func TestRecordingStorageBoundDoesNotBlockUpload(t *testing.T) {
	s := testRecordingStore(t)
	s.cfg.Managed = true
	s.cfg.CapacityDir = t.TempDir()
	f, e := os.Create(filepath.Join(s.cfg.CapacityDir, "synthetic-full"))
	if e != nil {
		t.Fatal(e)
	}
	if e = f.Truncate(513 * 1024 * 1024); e != nil {
		f.Close()
		t.Fatal(e)
	}
	f.Close()
	if slot, e := s.acquireWork(true); e == nil {
		releaseWork(slot)
		t.Fatal("full spool accepted another capture")
	}
	slot, e := s.acquireWork(false)
	if e != nil {
		t.Fatal("cannot drain a full spool")
	}
	releaseWork(slot)
}
