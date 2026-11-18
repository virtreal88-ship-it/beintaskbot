//go:build unifiedlab

package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"

	"go.mau.fi/whatsmeow/types"
	"wacalls/internal/unified"
)

var labRequestID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

func (m *messageLab) callPeer() string {
	peer := ""
	for _, raw := range m.cfg.AllowedChats {
		jid, err := types.ParseJID(raw)
		if err == nil && jid.Server == types.DefaultUserServer && directPeer(raw) {
			if peer != "" {
				return ""
			}
			peer = raw
		}
	}
	return peer
}

// Runs before provider clients are restored. The option exists only in the lab
// build and cannot alter a managed source's bounds or authorize another number.
func (m *messageLab) prepareCallTests(s *server) error {
	if m == nil || !m.cfg.CallTests {
		return nil
	}
	if m.callPeer() == "" || s.recordings == nil || s.recordings.cfg.Managed || s.recordings.cfg.Account != m.cfg.Scope.Account {
		return unified.ErrConfiguration
	}
	peers := make([]string, 0)
	for _, peer := range m.cfg.AllowedChats {
		if !directPeer(peer) {
			continue
		}
		found := false
		for _, allowed := range s.recordings.cfg.Peers {
			if allowed == peer {
				found = true
			}
		}
		if !found {
			return unified.ErrConfiguration
		}
		peers = append(peers, peer)
	}
	if _, err := s.sessions.store.db.Exec(`CREATE TABLE IF NOT EXISTS app_commands (id TEXT PRIMARY KEY, request TEXT NOT NULL, state TEXT NOT NULL, result BLOB)`); err != nil {
		return err
	}
	s.recordings.extendedLab = true
	m.calls = &appGateway{server: s, cfg: appGatewayConfig{Account: m.cfg.Scope.Account, Session: m.cfg.SessionID, Peers: peers}}
	return nil
}

type labCallRequest struct {
	Operation string `json:"operation"`
	Session   string `json:"session_id"`
	Peer      string `json:"peer"`
	SDP       string `json:"sdp_offer"`
	Consent   bool   `json:"consent_confirmed"`
}

func (m *messageLab) validateCall(r labCallRequest) bool {
	return m.cfg.CallTests && labRequestID.MatchString(r.Operation) && r.Session == m.cfg.SessionID &&
		r.Peer != "" && r.Peer == m.callPeer() && r.Consent && strings.HasPrefix(r.SDP, "v=0") && len(r.SDP) <= 24000
}

func (s *server) registerLabCallTests(mux *http.ServeMux, m *messageLab) {
	// Legacy arbitrary outgoing /api/sessions/{sid}/calls stays blocked.
	mux.HandleFunc("POST /api/unified-lab/calls", func(w http.ResponseWriter, r *http.Request) {
		if !m.cfg.CallTests || m.calls == nil {
			http.NotFound(w, r)
			return
		}
		var input labCallRequest
		decoder := json.NewDecoder(io.LimitReader(r.Body, 28001))
		decoder.DisallowUnknownFields()
		if decoder.Decode(&input) != nil || decoder.Decode(new(any)) != io.EOF || !m.validateCall(input) {
			writeJSON(w, 403, map[string]string{"error": "test_call_scope_denied"})
			return
		}
		sess, ready := s.labReady(m)
		if !ready {
			writeJSON(w, 409, map[string]string{"error": "test_session_unavailable"})
			return
		}
		cmd := appCommand{ID: input.Operation, CallID: input.Operation, Op: "start", Peer: input.Peer}
		cmd.Payload.SDP = input.SDP
		cmd.Payload.Consent = true
		ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
		defer cancel()
		result := m.executeCallTest(ctx, sess, cmd)
		code := 200
		if !result.OK {
			code = 409
		}
		writeJSON(w, code, result)
	})
}

func (m *messageLab) executeCallTest(ctx context.Context, sess *Session, cmd appCommand) appResult {
	m.callMu.Lock()
	defer m.callMu.Unlock()
	// Admission and journal execution are one serialized operation. A fresh UUID
	// cannot route around an unknown provider start, including after restart.
	var uncertain int
	if err := m.calls.server.sessions.store.db.QueryRow(`SELECT count(*) FROM app_commands WHERE state='executing' AND id<>?`, cmd.ID).Scan(&uncertain); err != nil || uncertain != 0 {
		return appResult{ID: cmd.ID, Error: "previous_test_call_unconfirmed", NotStarted: true}
	}
	return m.calls.executeOwned(ctx, sess, cmd, "lab-owner")
}
