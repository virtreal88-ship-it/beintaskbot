package main

import (
	"context"
	"encoding/json"
	"errors"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"google.golang.org/protobuf/proto"
	"io"
	"net/http"
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
	if kind == "" || size == 0 || size > unified.MaxMediaBytes || len(mime) > 120 || strings.ContainsAny(mime, "\r\n") {
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
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	content, err := session.client.DownloadAny(ctx, &message)
	if err != nil || uint64(len(content)) != size {
		http.Error(w, "native_media_unavailable", 503)
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", "attachment")
	w.Header().Set("Content-Security-Policy", "sandbox; default-src 'none'")
	w.Write(content)
}

// Called only after the existing private host/token/cookie/origin guard.
func (s *server) handleManagedMessage(w http.ResponseWriter, r *http.Request) {
	g := s.appGateway
	if g == nil || g.messages == nil || g.cfg.Messaging == nil {
		http.Error(w, "native_messaging_disabled", 409)
		return
	}
	var request unified.Outgoing
	decoder := json.NewDecoder(io.LimitReader(r.Body, 15<<20))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&request) != nil || decoder.Decode(new(any)) != io.EOF || request.Validate(g.cfg.Messaging.Scope) != nil {
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
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	result, err := g.messages.Deliver(ctx, session.client, request)
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
