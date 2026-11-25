package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"google.golang.org/protobuf/proto"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
	"wacalls/internal/unified"
)

type managedMessagingConfig struct {
	Scope unified.Scope `json:"scope"`
	Key   []byte        `json:"key"`
}

// Media is resolved from retained encrypted provider evidence in this journal.
// The caller cannot supply a download URL/key or read another conversation.
func (s *server) handleManagedMedia(w http.ResponseWriter, r *http.Request) {
	g := s.appGateway
	if g == nil || g.messages == nil || g.cfg.Messaging == nil {
		http.Error(w, "native_messaging_disabled", 409)
		return
	}
	var request struct {
		Scope unified.Scope `json:"scope"`
		Event string        `json:"event_id"`
		Chat  string        `json:"chat"`
	}
	decoder := json.NewDecoder(io.LimitReader(r.Body, 4097))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&request) != nil || decoder.Decode(new(any)) != io.EOF || request.Scope != g.cfg.Messaging.Scope || len(request.Event) != 64 || request.Chat == "" {
		http.Error(w, "native_media_invalid", 400)
		return
	}
	event, err := g.messages.Event(r.Context(), request.Event)
	var envelope unified.Envelope
	var message waE2E.Message
	if err != nil || json.Unmarshal(event.Payload, &envelope) != nil || envelope.Chat != request.Chat || envelope.Kind != "message" || proto.Unmarshal(envelope.Proto, &message) != nil {
		http.Error(w, "native_media_not_found", 404)
		return
	}
	kind, mime, size, _ := unified.MediaFacts(&message)
	video := kind == "video" && mime == "video/mp4"
	maxSize := uint64(unified.MaxMediaBytes)
	if video {
		maxSize = unified.MaxVideoBytes
	}
	if kind == "" || size == 0 || size > maxSize || len(mime) > 120 || strings.ContainsAny(mime, "\r\n") {
		http.Error(w, "native_media_restricted", 400)
		return
	}
	infos := s.sessions.infos()
	if len(infos) != 1 || infos[0].State != "open" {
		http.Error(w, "source_unavailable", 409)
		return
	}
	session, ok := s.sessions.Get(infos[0].ID)
	if !ok || !session.transportReady() || session.client.Store.ID == nil || session.client.Store.ID.ToNonAD().String() != g.cfg.Account {
		http.Error(w, "source_identity_mismatch", 409)
		return
	}
	timeout := 30 * time.Second
	parent := r.Context()
	if video && size > unified.MaxMediaBytes {
		timeout = 110 * time.Second
		parent = unified.WithVideoTransfer(parent)
	}
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	content, err := session.client.DownloadAny(ctx, &message)
	if err != nil || uint64(len(content)) != size {
		http.Error(w, "native_media_unavailable", 503)
		return
	}
	contentType := "application/octet-stream"
	if video {
		contentType = "video/mp4"
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Content-Length", strconv.Itoa(len(content)))
	w.Header().Set("Content-Disposition", "attachment")
	w.Header().Set("Content-Security-Policy", "sandbox; default-src 'none'")
	w.Write(content)
}

// Binary request keeps large video bytes out of JSON and the operation journal.
func decodeManagedOutgoing(r *http.Request, scope unified.Scope) (unified.Outgoing, error) {
	var command unified.Outgoing
	if r.URL.Path == "/internal/messages/video" {
		encoded := r.Header.Get("X-Native-Video-Command")
		if r.Header.Get("Content-Type") != "application/octet-stream" || r.Header.Get("Content-Encoding") != "" ||
			r.ContentLength <= 0 || r.ContentLength > unified.MaxVideoBytes || len(encoded) == 0 || len(encoded) > 28000 {
			return command, unified.ErrConfiguration
		}
		metadata, err := base64.StdEncoding.Strict().DecodeString(encoded)
		if err != nil {
			return command, unified.ErrConfiguration
		}
		decoder := json.NewDecoder(strings.NewReader(string(metadata)))
		decoder.DisallowUnknownFields()
		if decoder.Decode(&command) != nil || decoder.Decode(new(any)) != io.EOF || command.Scope != scope ||
			command.Kind != "video" || command.MIME != "video/mp4" || len(command.Data) != 0 {
			return command, unified.ErrConfiguration
		}
		command.Data = make([]byte, int(r.ContentLength))
		if _, err := io.ReadFull(r.Body, command.Data); err != nil {
			return command, unified.ErrConfiguration
		}
		// Beintaskbot also checks the MP4 signature before dispatch.
		if len(command.Data) <= 12 || string(command.Data[4:8]) != "ftyp" {
			return command, unified.ErrMediaRestricted
		}
	} else {
		decoder := json.NewDecoder(io.LimitReader(r.Body, 15<<20))
		decoder.DisallowUnknownFields()
		if decoder.Decode(&command) != nil || decoder.Decode(new(any)) != io.EOF || len(command.Data) > unified.MaxMediaBytes {
			return command, unified.ErrConfiguration
		}
	}
	return command, command.Validate(scope)
}

// Called only after the existing private host/token/cookie/origin guard.
func (s *server) handleManagedMessage(w http.ResponseWriter, r *http.Request) {
	g := s.appGateway
	if g == nil || g.messages == nil || g.cfg.Messaging == nil {
		http.Error(w, "native_messaging_disabled", 409)
		return
	}
	request, err := decodeManagedOutgoing(r, g.cfg.Messaging.Scope)
	if err != nil {
		http.Error(w, "native_message_invalid", 400)
		return
	}
	infos := s.sessions.infos()
	if len(infos) != 1 || infos[0].State != "open" {
		http.Error(w, "source_unavailable", 409)
		return
	}
	session, ok := s.sessions.Get(infos[0].ID)
	if !ok || !session.transportReady() || session.client.Store.ID == nil || session.client.Store.ID.ToNonAD().String() != g.cfg.Account {
		http.Error(w, "source_identity_mismatch", 409)
		return
	}
	timeout := 30 * time.Second
	if len(request.Data) > unified.MaxMediaBytes {
		timeout = 115 * time.Second
	}
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()
	result, err := g.messages.Deliver(ctx, session.client, request, session.client.Store.LIDs)
	if errors.Is(err, unified.ErrQuoteUnavailable) {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]any{
			"error": "native_quote_not_sent", "confirmed": false,
		})
		return
	}
	code := 200
	if errors.Is(err, unified.ErrConflict) {
		code = 409
	} else if err != nil {
		code = 503
	}
	writeJSON(w, code, map[string]any{"result": result, "confirmed": err == nil})
}

func (s *server) handleManagedMessageStatus(w http.ResponseWriter, r *http.Request) {
	g := s.appGateway
	if g == nil || g.messages == nil || g.cfg.Messaging == nil {
		http.Error(w, "native_messaging_disabled", 409)
		return
	}
	var request struct {
		Scope     unified.Scope `json:"scope"`
		Operation string        `json:"operation"`
		Chat      string        `json:"chat"`
	}
	decoder := json.NewDecoder(io.LimitReader(r.Body, 4097))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&request) != nil || decoder.Decode(new(any)) != io.EOF || request.Scope != g.cfg.Messaging.Scope {
		http.Error(w, "native_message_invalid", 400)
		return
	}
	result, err := g.messages.Lookup(r.Context(), request.Operation, request.Chat)
	if err != nil {
		http.Error(w, "native_receipt_unavailable", 404)
		return
	}
	writeJSON(w, 200, map[string]any{"result": result, "confirmed": result.State == "accepted"})
}
