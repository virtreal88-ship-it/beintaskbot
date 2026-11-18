package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"sync/atomic"
	"testing"
	"time"
)

func TestRecordingUploadDoesNotBlockGatewayControl(t *testing.T) {
	g, sess := testGateway(t)
	h, err := g.server.recordings.begin(sess.id, "upload-provider", g.cfg.Account, g.cfg.Peers[0])
	if err != nil {
		t.Fatal(err)
	}
	h.capture.Add(0, []float32{.2}, time.Now())
	h.finish(false)
	var polls atomic.Int32
	upload, release := make(chan struct{}, 1), make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/recording" {
			select {
			case upload <- struct{}{}:
			default:
			}
			<-release
			w.Write([]byte(`{"result":{}}`))
			return
		}
		polls.Add(1)
		json.NewEncoder(w).Encode(map[string]any{"result": map[string]any{"commands": []any{}, "recordings": []map[string]any{{"call_id": randomUUID(), "provider_id": "upload-provider", "saved": false}}}})
	}))
	defer server.Close()
	g.cfg.Endpoint = server.URL
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() { g.run(ctx); close(done) }()
	select {
	case <-upload:
	case <-time.After(3 * time.Second):
		close(release)
		t.Fatal("upload did not start")
	}
	deadline := time.Now().Add(2 * time.Second)
	for polls.Load() < 2 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	close(release)
	cancel()
	<-done
	if polls.Load() < 2 {
		t.Fatal("recording upload blocked control heartbeat")
	}
}

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

func TestManagedDeliveredRecordingsDoNotExhaustLabQuota(t *testing.T) {
	s := testRecordingStore(t)
	s.cfg.Managed = true
	s.cfg.CapacityDir = t.TempDir()
	g := &appGateway{cfg: appGatewayConfig{Managed: true, Session: "current"}, server: &server{recordings: s}}
	for i := 0; i < 21; i++ {
		provider := "delivered-" + strconv.Itoa(i)
		h, err := s.begin("current", provider, s.cfg.Account, s.cfg.Peers[0])
		if err != nil {
			t.Fatal("delivered tombstones blocked next capture", i, err)
		}
		h.capture.Add(0, []float32{.2}, time.Now())
		h.finish(false)
		g.acknowledgeRecording(provider)
	}
	var count int
	if err := s.db.QueryRow("SELECT count(*) FROM recordings WHERE state='delivered' AND encrypted IS NULL").Scan(&count); err != nil || count != 21 {
		t.Fatal("acknowledged records lost or retained audio", count, err)
	}
	if _, err := s.begin("current", "delivered-0", s.cfg.Account, s.cfg.Peers[0]); err == nil {
		t.Fatal("delivered call replay created a second capture")
	}
}

func TestPendingAndLegacyRecordingsKeepCountBound(t *testing.T) {
	for _, managed := range []bool{false, true} {
		t.Run(strconv.FormatBool(managed), func(t *testing.T) {
			s := testRecordingStore(t)
			s.cfg.Managed = managed
			s.cfg.CapacityDir = t.TempDir()
			for i := 0; i < 20; i++ {
				h, err := s.begin("pending", "provider-"+strconv.Itoa(i), s.cfg.Account, s.cfg.Peers[0])
				if err != nil {
					t.Fatal(err)
				}
				h.capture.Add(0, []float32{.2}, time.Now())
				h.finish(false)
			}
			if h, err := s.begin("pending", "over-limit", s.cfg.Account, s.cfg.Peers[0]); err == nil {
				h.finish(false)
				t.Fatal("unacknowledged recordings exceeded count bound")
			}
		})
	}
}
