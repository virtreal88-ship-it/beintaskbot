//go:build unifiedlab

package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
	"wacalls/internal/unified"
)

var messageLabConfig = flag.String("unified-lab-config", "", "private isolated test-number messaging config; unavailable in production builds")

//go:embed message_lab.html
var messageLabHTML string

type messageLabSettings struct {
	Scope        unified.Scope `json:"scope"`
	SessionID    string        `json:"session_id"`
	AllowedChats []string      `json:"allowed_chats"`
	Key          []byte        `json:"key"`
}
type messageLab struct {
	cfg         messageLabSettings
	journal     *unified.Journal
	diagnostics labDiagnostics
	pairMu      sync.Mutex
}

func newMessageLab(ctx context.Context, db *sql.DB, gateway string) (*messageLab, error) {
	if *messageLabConfig == "" {
		return nil, nil
	}
	// Lab auth is not SaaS auth. Never expose this experiment through appGateway.
	if gateway != "" {
		return nil, unified.ErrConfiguration
	}
	file, err := os.Open(*messageLabConfig)
	if err != nil {
		return nil, unified.ErrConfiguration
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 8192 || info.Mode().Perm()&0077 != 0 {
		return nil, unified.ErrConfiguration
	}
	var cfg messageLabSettings
	decoder := json.NewDecoder(io.LimitReader(file, 8193))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&cfg) != nil || decoder.Decode(new(any)) != io.EOF {
		return nil, unified.ErrConfiguration
	}
	if cfg.SessionID == "" || len(cfg.AllowedChats) < 1 || len(cfg.AllowedChats) > 8 {
		return nil, unified.ErrConfiguration
	}
	account, err := types.ParseJID(cfg.Scope.Account)
	if err != nil || account.Server != types.DefaultUserServer || account.User == "" || account.Device != 0 {
		return nil, unified.ErrConfiguration
	}
	for _, raw := range cfg.AllowedChats {
		chat, err := types.ParseJID(raw)
		if err != nil || chat.User == "" || chat.Device != 0 || (chat.Server != types.DefaultUserServer && chat.Server != types.HiddenUserServer && chat.Server != types.GroupServer) {
			return nil, unified.ErrConfiguration
		}
	}
	// A mistaken DB path must not restore any other linked device. Do this before
	// SessionManager.Restore connects clients, not after the first incoming event.
	rows, err := db.QueryContext(ctx, "SELECT id,COALESCE(jid,'') FROM sessions")
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var id, rawJID string
		if err := rows.Scan(&id, &rawJID); err != nil {
			rows.Close()
			return nil, err
		}
		if id != cfg.SessionID {
			rows.Close()
			return nil, unified.ErrConfiguration
		}
		if rawJID != "" {
			jid, err := types.ParseJID(rawJID)
			if err != nil || jid.ToNonAD().String() != cfg.Scope.Account {
				rows.Close()
				return nil, unified.ErrConfiguration
			}
		}
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return nil, err
	}
	rows.Close()
	journal, err := unified.New(ctx, db, cfg.Scope, cfg.Key)
	if err != nil {
		return nil, err
	}
	return &messageLab{cfg: cfg, journal: journal}, nil
}

func (m *messageLab) allows(chat types.JID) bool {
	for _, allowed := range m.cfg.AllowedChats {
		if chat.String() == allowed {
			return true
		}
	}
	return false
}
func (m *messageLab) matches(s *Session) bool {
	return s.id == m.cfg.SessionID && s.client.Store.ID != nil && s.client.Store.ID.ToNonAD().String() == m.cfg.Scope.Account
}

func (m *messageLab) attach(s *Session) bool {
	if s.id != m.cfg.SessionID {
		return false
	}
	client := s.client
	client.PrePairCallback = func(jid types.JID, _, _ string) bool {
		return jid.ToNonAD().String() == m.cfg.Scope.Account
	}
	client.Log = labProviderLogger{diagnostics: &m.diagnostics}
	client.SynchronousAck = true
	client.EnableDecryptedEventBuffer = true
	client.SetMediaHTTPClient(unified.NewMediaHTTPClient())
	unified.ProtectDevice(client.Store, m.journal, m.cfg.Scope.Account)
	client.AddEventHandlerWithSuccessStatus(func(raw any) bool {
		if s.client != client {
			return true
		}
		if client.Store.ID != nil && client.Store.ID.ToNonAD().String() != m.cfg.Scope.Account {
			go client.Disconnect()
			return false
		}
		ok := m.capture(s.mgr.appCtx, raw)
		s.handleEvent(raw) // Existing call lifecycle uses this same client.
		return ok
	})
	return true
}

type labEnvelope struct {
	Kind         string `json:"kind"`
	Chat         string `json:"chat"`
	Sender       string `json:"sender,omitempty"`
	SenderAlt    string `json:"sender_alt,omitempty"`
	RecipientAlt string `json:"recipient_alt,omitempty"`
	ProviderID   string `json:"provider_id"`
	FromMe       bool   `json:"from_me"`
	Timestamp    int64  `json:"timestamp"`
	Text         string `json:"text,omitempty"`
	Status       string `json:"status,omitempty"`
	Proto        []byte `json:"proto,omitempty"` // encrypted in SQLite; never returned by HTTP.
	MediaKind    string `json:"media_kind,omitempty"`
	MediaMime    string `json:"media_mime,omitempty"`
	MediaSize    uint64 `json:"media_size,omitempty"`
	FileName     string `json:"file_name,omitempty"`
}

func (m *messageLab) capture(ctx context.Context, raw any) bool {
	var envelope labEnvelope
	var identity any
	switch event := raw.(type) {
	case *events.Message:
		m.diagnostics.add("message_seen")
		if !m.allows(event.Info.Chat) {
			m.diagnostics.add("message_chat_denied")
			return true
		}
		// Do not retain view-once or expiring content in this research journal.
		if event.IsViewOnce || event.IsViewOnceV2 || event.IsViewOnceV2Extension || event.IsEphemeral {
			m.diagnostics.add("message_expiring_ignored")
			return true
		}
		data, err := proto.MarshalOptions{Deterministic: true}.Marshal(event.Message)
		if err != nil || event.Info.ID == "" {
			return false
		}
		envelope = labEnvelope{Kind: "message", Chat: event.Info.Chat.String(), Sender: event.Info.Sender.String(),
			SenderAlt: event.Info.SenderAlt.String(), RecipientAlt: event.Info.RecipientAlt.String(),
			ProviderID: event.Info.ID, FromMe: event.Info.IsFromMe, Timestamp: event.Info.Timestamp.UnixMilli(),
			Text: event.Message.GetConversation(), Proto: data}
		if envelope.Text == "" {
			envelope.Text = event.Message.GetExtendedTextMessage().GetText()
		}
		envelope.MediaKind, envelope.MediaMime, envelope.MediaSize, envelope.FileName = labMediaFacts(event.Message)
		// An edit of the same provider ID is a different event; redelivery is not.
		variant := "original"
		if event.IsEdit || event.Message.GetProtocolMessage() != nil || event.Message.GetReactionMessage() != nil {
			hash := sha256.Sum256(data)
			variant = hex.EncodeToString(hash[:])
		}
		identity = []any{"message", envelope.Chat, envelope.Sender, envelope.FromMe, envelope.ProviderID, variant}
	case *events.Receipt:
		m.diagnostics.add("receipt_seen")
		if !m.allows(event.Chat) {
			m.diagnostics.add("receipt_chat_denied")
			return true
		}
		for _, id := range event.MessageIDs {
			envelope = labEnvelope{Kind: "receipt", Chat: event.Chat.String(), Sender: event.Sender.String(), ProviderID: id,
				FromMe: event.IsFromMe, Timestamp: event.Timestamp.UnixMilli(), Status: string(event.Type)}
			identity = []any{"receipt", envelope.Chat, envelope.Sender, id, envelope.Status, envelope.Timestamp}
			if !m.append(ctx, identity, envelope) {
				return false
			}
		}
		return true
	case *events.UndecryptableMessage:
		m.diagnostics.add("undecryptable_event")
		return true
	case *events.Connected:
		m.diagnostics.add("connected_event")
		return true
	case *events.Disconnected:
		m.diagnostics.add("disconnected_event")
		return true
	default:
		m.diagnostics.add("other_event")
		return true
	}
	return m.append(ctx, identity, envelope)
}

func (m *messageLab) append(ctx context.Context, identity any, envelope labEnvelope) bool {
	key, _ := json.Marshal(identity)
	data, err := json.Marshal(envelope)
	if err != nil {
		return false
	}
	_, err = m.journal.Append(ctx, string(key), data)
	if err != nil {
		m.diagnostics.add("journal_append_failed")
	} else {
		m.diagnostics.add("journal_append_confirmed")
	}
	return err == nil
}

// The legacy call UI can create/delete arbitrary lab slots. A unified experiment
// is pinned to the previously verified slot; even authenticated callers must not
// start a second client outside its account guard. Re-pairing that slot is allowed.
func (s *server) guardMessageLab(next http.Handler) http.Handler {
	if s.sessions == nil || s.sessions.messageLab == nil {
		return next
	}
	m := s.sessions.messageLab
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
		if len(parts) >= 2 && parts[0] == "api" && parts[1] == "sessions" {
			denied := len(parts) == 2 && r.Method != http.MethodGet
			if len(parts) >= 3 {
				denied = parts[2] != m.cfg.SessionID || (len(parts) == 3 && r.Method == http.MethodDelete) || (len(parts) == 4 && parts[3] == "logout")
			}
			if denied {
				writeJSON(w, 403, map[string]string{"error": "test_session_pinned"})
				return
			}
			if len(parts) == 4 && parts[3] == "pair" && r.Method == http.MethodPost {
				s.handleLabPair(w, r, m)
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

func (s *server) registerMessageLab(mux *http.ServeMux) {
	if s.sessions == nil || s.sessions.messageLab == nil {
		return
	}
	m := s.sessions.messageLab
	s.registerLabMedia(mux, m)
	s.registerLabReceiveProbe(mux, m)
	mux.HandleFunc("POST /api/unified-lab/pair", func(w http.ResponseWriter, r *http.Request) { s.handleLabPair(w, r, m) })
	mux.HandleFunc("GET /unified-lab", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		io.WriteString(w, messageLabHTML)
	})
	mux.HandleFunc("GET /api/unified-lab/status", func(w http.ResponseWriter, r *http.Request) {
		session, ok := s.sessions.Get(m.cfg.SessionID)
		ready := ok && m.matches(session) && session.client.IsConnected()
		writeJSON(w, 200, map[string]any{"ready": ready, "chats": m.cfg.AllowedChats, "session_id": m.cfg.SessionID, "diagnostics": m.diagnostics.snapshot()})
	})
	mux.HandleFunc("GET /api/unified-lab/events", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		after, err := strconv.ParseInt(r.URL.Query().Get("after"), 10, 64)
		if r.URL.Query().Get("after") == "" {
			after, err = 0, nil
		}
		if err != nil {
			writeJSON(w, 400, map[string]string{"error": "invalid_cursor"})
			return
		}
		items, err := m.journal.Events(r.Context(), after, 30)
		if err != nil {
			writeJSON(w, 503, map[string]string{"error": "journal_unavailable"})
			return
		}
		nextCursor := after
		visible := make([]unified.Event, 0, len(items))
		for index := range items {
			nextCursor = items[index].Sequence
			var envelope labEnvelope
			if json.Unmarshal(items[index].Payload, &envelope) != nil {
				writeJSON(w, 503, map[string]string{"error": "journal_invalid"})
				return
			}
			chat, parseErr := types.ParseJID(envelope.Chat)
			if parseErr != nil || !m.allows(chat) {
				continue
			}
			envelope.Proto = nil
			items[index].Payload, _ = json.Marshal(envelope)
			visible = append(visible, items[index])
		}
		writeJSON(w, 200, map[string]any{"events": visible, "next_cursor": nextCursor})
	})
	mux.HandleFunc("POST /api/unified-lab/text", func(w http.ResponseWriter, r *http.Request) {
		var request struct {
			Operation string `json:"operation"`
			Chat      string `json:"chat"`
			Text      string `json:"text"`
		}
		decoder := json.NewDecoder(io.LimitReader(r.Body, 16385))
		decoder.DisallowUnknownFields()
		if decoder.Decode(&request) != nil || decoder.Decode(new(any)) != io.EOF || strings.TrimSpace(request.Text) == "" || len(request.Text) > 4096 {
			writeJSON(w, 400, map[string]string{"error": "invalid_request"})
			return
		}
		chat, err := types.ParseJID(request.Chat)
		if err != nil || !m.allows(chat) {
			writeJSON(w, 403, map[string]string{"error": "test_peer_denied"})
			return
		}
		session, ok := s.sessions.Get(m.cfg.SessionID)
		if !ok || !m.matches(session) || !session.client.IsConnected() {
			writeJSON(w, 409, map[string]string{"error": "test_session_unavailable"})
			return
		}
		message := &waE2E.Message{Conversation: proto.String(request.Text)}
		payload, _ := proto.MarshalOptions{Deterministic: true}.Marshal(message)
		ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
		defer cancel()
		result, err := m.journal.Send(ctx, request.Operation, request.Chat, payload, session.client.GenerateMessageID, func(ctx context.Context, id string) error {
			response, err := session.client.SendMessage(ctx, chat, message, whatsmeow.SendRequestExtra{ID: id})
			if err != nil || response.ID != id {
				return unified.ErrUnknown
			}
			return nil
		})
		code := 200
		if errors.Is(err, unified.ErrConflict) {
			code = 409
		} else if err != nil {
			code = 503
		}
		writeJSON(w, code, map[string]any{"result": result, "confirmed": err == nil})
	})
}
