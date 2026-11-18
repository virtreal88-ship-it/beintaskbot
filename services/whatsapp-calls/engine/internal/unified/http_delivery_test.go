package unified

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

func TestHTTPDeliveryRetainsCursorUntilExactReceipt(t *testing.T) {
	ctx := context.Background()
	j := journal(t, database(t, filepath.Join(t.TempDir(), "events.db")), testScope)
	appendFixture(t, j, "one")
	original := next(t, j)
	mode := "failure"
	requests := 0
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.Method != "POST" || r.URL.Path != "/api/platform/call-gateway/messages" || r.Header.Get("Authorization") != "Bearer "+strings.Repeat("x", 40) {
			t.Error("bad private request")
		}
		var d Delivery
		if json.NewDecoder(r.Body).Decode(&d) != nil || d.Event.ID != original.Event.ID || d.Proof != original.Proof {
			t.Error("mutated retry")
		}
		w.Header().Set("Content-Type", "application/json")
		receipt := stored(&d)
		switch mode {
		case "failure":
			w.WriteHeader(503)
			fmt.Fprint(w, `{"error":"SYNTHETIC_PRIVATE_DATA"}`)
		case "malformed":
			fmt.Fprint(w, `{"state":"stored"`)
		case "extra":
			raw, _ := json.Marshal(receipt)
			fmt.Fprintf(w, "%s{}", raw)
		case "oversized":
			fmt.Fprint(w, strings.Repeat(" ", 8193))
		case "scope":
			receipt.Scope.Source = "another"
			json.NewEncoder(w).Encode(receipt)
		case "event":
			receipt.EventID = strings.Repeat("e", 64)
			json.NewEncoder(w).Encode(receipt)
		case "wrongtype":
			w.Header().Set("Content-Type", "text/html")
			json.NewEncoder(w).Encode(receipt)
		default:
			json.NewEncoder(w).Encode(receipt)
		}
	}))
	defer srv.Close()
	for _, mode = range []string{"failure", "malformed", "extra", "oversized", "scope", "event", "wrongtype"} {
		ok, err := j.DeliverOnce(ctx, srv.Client(), srv.URL+"/api/platform/call-gateway/messages", strings.Repeat("x", 40))
		if ok || err == nil || strings.Contains(err.Error(), "SYNTHETIC_PRIVATE_DATA") {
			t.Fatal("unconfirmed delivery acknowledged")
		}
		if next(t, j).After != original.After {
			t.Fatal("cursor advanced")
		}
	}
	mode = "stored"
	if ok, err := j.DeliverOnce(ctx, srv.Client(), srv.URL+"/api/platform/call-gateway/messages", strings.Repeat("x", 40)); !ok || err != nil {
		t.Fatal(err)
	}
	if d, err := j.NextDelivery(ctx); err != nil || d != nil {
		t.Fatal("successful receipt not retained")
	}
	before := requests
	if ok, err := j.DeliverOnce(ctx, srv.Client(), srv.URL+"/api/platform/call-gateway/messages", strings.Repeat("x", 40)); ok || err != nil || requests != before {
		t.Fatal("empty inbox sent request")
	}
	if items, err := j.Events(ctx, 0, 10); err != nil || len(items) != 1 {
		t.Fatal("event deleted")
	}
}

func TestHTTPDeliveryNeverFollowsRedirect(t *testing.T) {
	j := journal(t, database(t, filepath.Join(t.TempDir(), "events.db")), testScope)
	appendFixture(t, j, "one")
	leaked := false
	destination := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { leaked = true }))
	defer destination.Close()
	redirect := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, destination.URL, http.StatusTemporaryRedirect)
	}))
	defer redirect.Close()
	ok, err := j.DeliverOnce(context.Background(), redirect.Client(), redirect.URL+"/api/platform/call-gateway/messages", strings.Repeat("x", 40))
	if ok || err == nil || leaked || next(t, j).After != 0 {
		t.Fatal("redirect leaked private data or acknowledged delivery")
	}
	for _, endpoint := range []string{"http://example.invalid/api/platform/call-gateway/messages", "https://example.invalid/other", "https://user@example.invalid/api/platform/call-gateway/messages", "https://example.invalid/api/platform/call-gateway/messages?token=x"} {
		if _, err := j.DeliverOnce(context.Background(), redirect.Client(), endpoint, strings.Repeat("x", 40)); err != ErrConfiguration {
			t.Fatal("untrusted endpoint admitted")
		}
	}
}
