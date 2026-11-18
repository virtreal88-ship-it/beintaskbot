//go:build unifiedlab

package main

import (
	"context"
	"go.mau.fi/whatsmeow/types"
	"net/http"
	"time"
)

// Discovery is not authorization to send. A matching name is insufficient:
// both exact, provider-confirmed participant numbers must be present, no others.
func (m *messageLab) testGroupCandidate(group *types.GroupInfo) bool {
	if group == nil || group.JID.Server != types.GroupServer || group.Name != "Beintaskbot TEST" || group.IsParent || group.IsEphemeral || group.Suspended || len(group.Participants) != 2 || (group.ParticipantCount != 0 && group.ParticipantCount != 2) {
		return false
	}
	peer := m.callPeer()
	if peer == "" || peer == m.cfg.Scope.Account {
		return false
	}
	seen := map[string]bool{}
	for _, participant := range group.Participants {
		pn := participant.PhoneNumber
		if pn.IsEmpty() && participant.JID.Server == types.DefaultUserServer {
			pn = participant.JID
		}
		if participant.Error != 0 || participant.AddRequest != nil || pn.Server != types.DefaultUserServer || pn.Device != 0 {
			return false
		}
		id := pn.String()
		if (id != peer && id != m.cfg.Scope.Account) || seen[id] {
			return false
		}
		seen[id] = true
	}
	return seen[peer] && seen[m.cfg.Scope.Account]
}

func (s *server) registerLabGroupDiscovery(mux *http.ServeMux, m *messageLab) {
	mux.HandleFunc("GET /api/unified-lab/test-group", func(w http.ResponseWriter, r *http.Request) {
		if !m.cfg.CallTests {
			http.NotFound(w, r)
			return
		}
		session, ready := s.labReady(m)
		if !ready {
			writeJSON(w, 409, map[string]string{"error": "test_session_unavailable"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
		defer cancel()
		groups, err := session.client.GetJoinedGroups(ctx)
		if err != nil {
			writeJSON(w, 502, map[string]string{"error": "test_group_lookup_failed"})
			return
		}
		candidates := make([]string, 0)
		for _, group := range groups {
			if m.testGroupCandidate(group) {
				candidates = append(candidates, group.JID.String())
			}
		}
		// No membership changes, auto-approval, histories or other group metadata.
		writeJSON(w, 200, map[string]any{"candidate_ids": candidates, "participant_count": 2})
	})
}
