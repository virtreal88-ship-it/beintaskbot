//go:build unifiedlab

package main

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"go.mau.fi/whatsmeow/types"
)

func TestLabReceiveProbeAdmission(t *testing.T) {
	_, s, _ := labFixture(t)
	token := strings.Repeat("x", 40)
	guard, _ := newLabGuard("http://127.0.0.1:8080", token)
	handler := guard.wrap(s.routes())
	for _, tc := range []struct {
		body, origin string
		auth         bool
		want         int
	}{
		{`{"action":"devices"}`, "", false, 401},
		{`{"action":"announce-offline"}`, "https://other.invalid", true, 403},
		{`{"action":"logout"}`, "", true, 400},
		{`{"action":"devices","account":"other"}`, "", true, 400},
		{`{"action":"devices"} {}`, "", true, 400},
		{`{"action":"devices"}`, "", true, 409},
		{`{"action":"announce-offline"}`, "", true, 409},
	} {
		r := httptest.NewRequest("POST", "http://127.0.0.1:8080/api/unified-lab/receive-probe", strings.NewReader(tc.body))
		if tc.auth {
			r.SetBasicAuth("lab", token)
		}
		if tc.origin != "" {
			r.Header.Set("Origin", tc.origin)
		}
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != tc.want {
			t.Fatalf("probe admission: %d != %d", w.Code, tc.want)
		}
	}
}

func TestLabDeviceFactsCannotMixAccountsOrExposeIDs(t *testing.T) {
	own := types.NewJID("15550000001", types.DefaultUserServer)
	own.Device = 25
	other := types.NewJID("15550000002", types.DefaultUserServer)
	other.Device = 25
	facts := labDeviceFacts(own, []types.JID{own.ToNonAD(), own, other})
	raw, _ := json.Marshal(facts)
	if facts["account_device_count"] != 2 || facts["local_device_returned"] != true || strings.Contains(string(raw), "1555") {
		t.Fatal("unsafe device facts")
	}
	if labDeviceFacts(own, []types.JID{other})["local_device_returned"] != false {
		t.Fatal("other account matched")
	}
}

func TestLabProbeOneAttemptUnderConcurrency(t *testing.T) {
	d := &labDiagnostics{}
	var accepted atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if d.once("probe_started_devices") {
				accepted.Add(1)
			}
		}()
	}
	wg.Wait()
	if accepted.Load() != 1 {
		t.Fatal("probe ran more than once")
	}
}
