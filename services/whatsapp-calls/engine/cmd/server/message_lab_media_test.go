//go:build unifiedlab

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"image"
	"image/png"
	"net/http/httptest"
	"strings"
	"testing"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

func TestLabMediaAdmissionAndProtocol(t *testing.T) {
	var pngData bytes.Buffer
	if err := png.Encode(&pngData, image.NewRGBA(image.Rect(0, 0, 2, 2))); err != nil {
		t.Fatal(err)
	}
	imageRequest := labMediaUpload{Kind: "image", MIME: "image/png", Name: "fixture.png", Data: pngData.Bytes()}
	if !validateLabMedia(imageRequest) {
		t.Fatal("valid bounded PNG denied")
	}
	invalid := imageRequest
	invalid.MIME = "image/jpeg"
	if validateLabMedia(invalid) {
		t.Fatal("MIME mismatch admitted")
	}
	invalid = imageRequest
	invalid.Name = "../fixture.png"
	if validateLabMedia(invalid) {
		t.Fatal("path name admitted")
	}
	invalid = imageRequest
	invalid.Data = bytes.Repeat([]byte{'x'}, labUploadLimit+1)
	if validateLabMedia(invalid) {
		t.Fatal("oversized media admitted")
	}
	if validateLabMedia(labMediaUpload{Kind: "document", MIME: "text/html", Data: []byte("<script>test</script>")}) {
		t.Fatal("active document admitted")
	}
	if validateLabMedia(labMediaUpload{Kind: "voice", MIME: "audio/ogg", Data: []byte("not opus")}) {
		t.Fatal("non Opus fixture admitted")
	}
	voice := make([]byte, 47)
	copy(voice, "OggS")
	voice[26] = 1
	voice[27] = 19
	copy(voice[28:], "OpusHead")
	r := labMediaUpload{Kind: "voice", MIME: "audio/ogg", Data: voice}
	if !validateLabMedia(r) {
		t.Fatal("Opus fixture header denied")
	}
	upload := whatsmeow.UploadResponse{URL: "https://mmg.whatsapp.net/private", MediaKey: []byte("test-secret"), FileLength: uint64(len(voice))}
	m := labUploadMessage(r, upload)
	if !m.GetAudioMessage().GetPTT() || m.GetAudioMessage().GetMimetype() != "audio/ogg; codecs=opus" {
		t.Fatal("voice protocol changed")
	}
}

func TestLabMediaRoutesFailClosedBeforeNetwork(t *testing.T) {
	m, s, _ := labFixture(t)
	token := strings.Repeat("x", 40)
	guard, _ := newLabGuard("http://127.0.0.1:8080", token)
	handler := guard.wrap(s.routes())
	request := func(method, path string, body []byte, auth bool, origin string) int {
		r := httptest.NewRequest(method, "http://127.0.0.1:8080"+path, bytes.NewReader(body))
		if auth {
			r.SetBasicAuth("lab", token)
		}
		if origin != "" {
			r.Header.Set("Origin", origin)
		}
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		return w.Code
	}
	peer := m.cfg.AllowedChats[0]
	body := func(chat string) []byte {
		b, _ := json.Marshal(labMediaUpload{Operation: "media-op", Chat: chat, Kind: "document", MIME: "text/plain", Name: "test.txt", Data: []byte("test fixture")})
		return b
	}
	for _, tc := range []struct {
		method, path string
		body         []byte
		auth         bool
		origin       string
		want         int
	}{
		{"POST", "/api/unified-lab/media", body(peer), false, "http://127.0.0.1:8080", 401},
		{"POST", "/api/unified-lab/media", body(peer), true, "https://other.invalid", 403},
		{"POST", "/api/unified-lab/media", body("15550000003@s.whatsapp.net"), true, "http://127.0.0.1:8080", 403},
		{"POST", "/api/unified-lab/media", body(peer), true, "http://127.0.0.1:8080", 409},
		{"POST", "/api/unified-lab/media", append(body(peer), []byte(` {}`)...), true, "http://127.0.0.1:8080", 400},
		{"GET", "/api/unified-lab/media/unknown", nil, false, "", 401},
		{"GET", "/api/unified-lab/media/unknown", nil, true, "", 404},
		{"GET", "/api/unified-lab/groups/123456@g.us", nil, true, "", 403},
		{"GET", "/api/unified-lab/groups/" + peer, nil, true, "", 403},
	} {
		if got := request(tc.method, tc.path, tc.body, tc.auth, tc.origin); got != tc.want {
			t.Errorf("%s %s: %d, want %d", tc.method, tc.path, got, tc.want)
		}
	}
	chat, _ := types.ParseJID(peer)
	e := &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: chat, Sender: chat}, ID: "media-safe"}, Message: &waE2E.Message{ImageMessage: &waE2E.ImageMessage{MediaKey: []byte("private-media-key"), FileLength: proto.Uint64(10), URL: proto.String("https://mmg.whatsapp.net/private")}}}
	if !m.capture(context.Background(), e) {
		t.Fatal("capture failed")
	}
	items, err := m.journal.Events(context.Background(), 0, 10)
	if err != nil || len(items) != 1 {
		t.Fatal("journal failed")
	}
	if got := request("GET", "/api/unified-lab/media/"+items[0].ID, nil, true, ""); got != 409 {
		t.Fatal("missing session did not block media")
	}
	// Revoking a peer after journal capture must also revoke its media access.
	m.cfg.AllowedChats = []string{"15550000003@s.whatsapp.net"}
	if got := request("GET", "/api/unified-lab/media/"+items[0].ID, nil, true, ""); got != 404 {
		t.Fatal("revoked peer media exposed")
	}
	r := httptest.NewRequest("GET", "http://127.0.0.1:8080/api/unified-lab/events", nil)
	r.SetBasicAuth("lab", token)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)
	var page struct {
		Events []json.RawMessage `json:"events"`
		Next   int64             `json:"next_cursor"`
	}
	if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &page) != nil || len(page.Events) != 0 || page.Next != items[0].Sequence {
		t.Fatal("revoked event exposed or filtering stalled cursor")
	}
}
