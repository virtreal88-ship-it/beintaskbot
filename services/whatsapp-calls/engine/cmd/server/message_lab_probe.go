//go:build unifiedlab

package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"time"

	"go.mau.fi/whatsmeow/types"
)

// These bounded manual probes use only the pinned test client. No caller may
// select an account, recipient or stanza. They neither send messages nor pair,
// delete, mark messages read, or change the account's privacy settings.
func (s *server) registerLabReceiveProbe(mux *http.ServeMux, m *messageLab) {
	mux.HandleFunc("POST /api/unified-lab/receive-probe", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		var request struct {
			Action string `json:"action"`
		}
		decoder := json.NewDecoder(io.LimitReader(r.Body, 257))
		decoder.DisallowUnknownFields()
		if decoder.Decode(&request) != nil || decoder.Decode(new(any)) != io.EOF || (request.Action != "devices" && request.Action != "announce-offline" && request.Action != "announce-online-bounded") {
			writeJSON(w, 400, map[string]string{"error": "invalid_probe"})
			return
		}
		session, ok := s.sessions.Get(m.cfg.SessionID)
		if !ok || !m.matches(session) || !session.client.IsConnected() || !session.client.IsLoggedIn() {
			writeJSON(w, 409, map[string]string{"error": "test_session_unavailable"})
			return
		}
		// One request of each kind per process, not a periodic provider poll.
		if !m.diagnostics.once("probe_started_" + request.Action) {
			writeJSON(w, 409, map[string]string{"error": "probe_already_attempted"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		defer cancel()
		if request.Action == "announce-online-bounded" {
			// Same captured client, never a later replacement. Cleanup runs even
			// when the requesting browser disconnects or the online write fails.
			client := session.client
			go labOnlineProbe(s.sessions.appCtx, 2*time.Minute, client.SendPresence, &m.diagnostics)
			writeJSON(w, 202, map[string]string{"status": "probe_started"})
			return
		}
		if request.Action == "devices" {
			own := *session.client.Store.ID
			devices, err := session.client.GetUserDevices(ctx, []types.JID{own.ToNonAD()})
			if err != nil {
				writeJSON(w, 503, map[string]string{"error": "device_query_failed"})
				return
			}
			// This pinned upstream's public contract allows excluding the local
			// device. Absence is inconclusive, not permission to reset the session.
			writeJSON(w, 200, labDeviceFacts(own, devices))
			return
		}
		if err := session.client.SendPresence(ctx, types.PresenceUnavailable); err != nil {
			writeJSON(w, 503, map[string]string{"error": "presence_write_failed"})
			return
		}
		writeJSON(w, 200, map[string]bool{"written": true}) // Socket write, not delivery proof.
	})
}

func labOnlineProbe(ctx context.Context, duration time.Duration, send func(context.Context, types.Presence) error, diagnostics *labDiagnostics) {
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if send(cleanup, types.PresenceUnavailable) != nil {
			diagnostics.add("probe_offline_write_failed")
		} else {
			diagnostics.add("probe_offline_written")
		}
	}()
	writeCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	err := send(writeCtx, types.PresenceAvailable)
	cancel()
	if err != nil {
		diagnostics.add("probe_online_write_failed")
		return
	}
	diagnostics.add("probe_online_written")
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
	case <-timer.C:
	}
}

func labDeviceFacts(own types.JID, devices []types.JID) map[string]any {
	count, local, primary := 0, false, false
	for _, device := range devices {
		if device.ToNonAD() != own.ToNonAD() {
			continue
		}
		count++
		local = local || device == own
		primary = primary || device.Device == 0
	}
	return map[string]any{"account_device_count": count, "local_device_returned": local, "primary_device_returned": primary}
}
