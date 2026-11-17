package main

import (
	"context"
	"github.com/coder/websocket"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestManagedStreamBothDirectionsAndClosure(t *testing.T) {
	got := make(chan []float32, 1)
	var ended atomic.Int32
	bridge := newStreamBridge(func(p []float32) { got <- p }, func() { ended.Add(1) })
	server := httptest.NewServer(http.HandlerFunc(bridge.stream.serve))
	defer server.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	socket, _, err := websocket.Dial(ctx, server.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer socket.CloseNow()
	if err = socket.Write(ctx, websocket.MessageBinary, []byte{0, 64, 0, 192}); err != nil {
		t.Fatal(err)
	}
	select {
	case pcm := <-got:
		if len(pcm) != 2 || pcm[0] < .4 || pcm[1] > -.4 {
			t.Fatal("PCM changed")
		}
	case <-ctx.Done():
		t.Fatal("missing microphone")
	}
	if err = bridge.WritePCM([]float32{.5, -.5}); err != nil {
		t.Fatal(err)
	}
	kind, raw, err := socket.Read(ctx)
	if err != nil || kind != websocket.MessageBinary || len(raw) != 4 {
		t.Fatal("missing remote audio")
	}
	second, response, e := websocket.Dial(ctx, server.URL, nil)
	if second != nil {
		second.CloseNow()
	}
	if e == nil || response.StatusCode != 409 {
		t.Fatal("duplicate attachment admitted")
	}
	bridge.Close()
	_, _, err = socket.Read(ctx)
	if err == nil {
		t.Fatal("transport remained open")
	}
	deadline := time.Now().Add(time.Second)
	for ended.Load() != 1 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if ended.Load() != 1 {
		t.Fatal("termination not delivered once")
	}
	if bridge.WritePCM([]float32{1}) == nil {
		t.Fatal("write after closure")
	}
}

func TestManagedStreamRejectsMalformedPCM(t *testing.T) {
	for _, kind := range []websocket.MessageType{websocket.MessageText, websocket.MessageBinary} {
		got := atomic.Int32{}
		bridge := newStreamBridge(func([]float32) { got.Add(1) }, nil)
		server := httptest.NewServer(http.HandlerFunc(bridge.stream.serve))
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		socket, _, err := websocket.Dial(ctx, server.URL, nil)
		if err != nil {
			t.Fatal(err)
		}
		socket.Write(ctx, kind, []byte{1})
		_, _, err = socket.Read(ctx)
		if err == nil || got.Load() != 0 {
			t.Fatal("invalid audio accepted")
		}
		socket.CloseNow()
		cancel()
		server.Close()
	}
}

func TestManagedGuardNeverExposesLegacyOrBrowserAPI(t *testing.T) {
	token := strings.Repeat("x", 48)
	handler := managedHandler(nil, "127.0.0.1:9001", token)
	for _, tc := range []struct {
		method, path, origin, cookie, auth string
		code                               int
	}{
		{"GET", "/api/sessions", "", "", token, 404},
		{"POST", "/api/sessions/s/calls", "", "", token, 404},
		{"GET", "/lab/login", "", "", token, 404},
		{"POST", "/internal/setup", "https://crm.pro.az", "", token, 403},
		{"GET", "/internal/status", "", "session=1", token, 403},
		{"GET", "/internal/status", "", "", "bad", 403},
	} {
		req := httptest.NewRequest(tc.method, "http://127.0.0.1:9001"+tc.path, nil)
		req.Header.Set("Authorization", "Bearer "+tc.auth)
		req.Header.Set("Origin", tc.origin)
		req.Header.Set("Cookie", tc.cookie)
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, req)
		if w.Code != tc.code {
			t.Fatalf("%s got %d", tc.path, w.Code)
		}
	}
}
