//go:build unifiedlab

package main

import "net/http"

// Both the existing call screen's Pair action and the lab button use one lock
// and the existing SessionManager pairing flow. No QR is generated on page load.
func (m *messageLab) pairOnce(state func() string, start func() error) (string, error) {
	m.pairMu.Lock()
	defer m.pairMu.Unlock()
	current := state()
	if current == "paired" || current == "pending" {
		return current, nil
	}
	if err := start(); err != nil {
		return "failed", err
	}
	return "pending", nil
}

func (s *server) handleLabPair(w http.ResponseWriter, r *http.Request, m *messageLab) {
	w.Header().Set("Cache-Control", "no-store")
	state, err := m.pairOnce(func() string {
		session, ok := s.sessions.Get(m.cfg.SessionID)
		if !ok {
			return "absent"
		}
		session.mu.Lock()
		auth := session.auth
		session.mu.Unlock()
		if auth.Paired || auth.State == "open" {
			return "paired"
		}
		if auth.State == "qr" || auth.State == "connecting" {
			return "pending"
		}
		return "retry"
	}, func() error {
		var err error
		if _, ok := s.sessions.Get(m.cfg.SessionID); ok {
			err = s.sessions.Pair(m.cfg.SessionID)
		} else {
			_, err = s.sessions.createWithID(m.cfg.SessionID, "Unified test slot")
		}
		if err != nil {
			if session, ok := s.sessions.Get(m.cfg.SessionID); ok {
				session.setAuth(AuthSnapshot{State: "pairing_failed"})
			}
		}
		return err
	})
	if err != nil {
		writeJSON(w, 503, map[string]string{"error": "test_pairing_failed"})
		return
	}
	writeJSON(w, 200, map[string]string{"state": state, "session_id": m.cfg.SessionID})
}
