package main

// The hosted engine is private to its supervisor. Browser identity and source
// permissions are checked in Worker; no lab UI, login or direct call API is exposed.
import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"net/http"
	"regexp"
	"rsc.io/qr"
	"strings"
	"sync"
)

var streamPath = regexp.MustCompile(`^/internal/audio/([A-Za-z0-9_-]{5,160})/([0-9a-f-]{36})$`)

func managedHandler(s *server, host, token string) http.Handler {
	digest := sha256.Sum256([]byte(token))
	var setup sync.Mutex
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		candidate := sha256.Sum256([]byte(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")))
		if r.Host != host || r.Header.Get("Origin") != "" || r.Header.Get("Cookie") != "" || !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") || subtle.ConstantTimeCompare(candidate[:], digest[:]) != 1 {
			http.Error(w, "source_access_denied", 403)
			return
		}
		if (r.Method == "POST" && r.URL.Path == "/internal/setup") || (r.Method == "GET" && r.URL.Path == "/internal/status") {
			setup.Lock()
			defer setup.Unlock()
			infos := s.sessions.infos()
			if len(infos) > 1 {
				http.Error(w, "source_identity_ambiguous", 409)
				return
			}
			if r.Method == "POST" {
				if len(infos) == 0 {
					if _, e := s.sessions.Create("Beintaskbot"); e != nil {
						http.Error(w, "source_pairing_failed", 503)
						return
					}
				} else if !infos[0].Paired && (infos[0].State == "timeout" || infos[0].State == "logged_out" || infos[0].State == "error") {
					if e := s.sessions.Pair(infos[0].ID); e != nil {
						http.Error(w, "source_pairing_failed", 503)
						return
					}
				}
				infos = s.sessions.infos()
			}
			out := map[string]any{"connected": false, "account_jid": "", "qr": ""}
			if len(infos) == 1 {
				sess, _ := s.sessions.Get(infos[0].ID)
				sess.mu.Lock()
				auth := sess.auth
				sess.mu.Unlock()
				if id := sess.client.Store.ID; id != nil {
					out["account_jid"] = id.ToNonAD().String()
				} else {
					if auth.QR != "" && len(auth.QR) <= 4096 {
						if code, e := qr.Encode(auth.QR, qr.M); e == nil {
							out["qr"] = base64.StdEncoding.EncodeToString(code.PNG())
						}
					}
				}
				out["connected"] = infos[0].State == "open" && sess.transportReady() && out["account_jid"] == s.appGateway.cfg.Account
				out["diagnostics"] = sess.diagnostics()
			}
			writeJSON(w, 200, out)
			return
		}
		if r.Method == "POST" && r.URL.Path == "/internal/messages/status" {
			s.handleManagedMessageStatus(w, r)
			return
		}
		if r.Method == "POST" && r.URL.Path == "/internal/messages/group" {
			s.handleManagedGroup(w, r)
			return
		}
		if r.Method == "POST" && r.URL.Path == "/internal/messages" {
			s.handleManagedMessage(w, r)
			return
		}
		if r.Method == "POST" && r.URL.Path == "/internal/messages/media" {
			s.handleManagedMedia(w, r)
			return
		}
		if match := streamPath.FindStringSubmatch(r.URL.Path); r.Method == "GET" && match != nil {
			infos := s.sessions.infos()
			if len(infos) != 1 || infos[0].State != "open" {
				http.Error(w, "source_unavailable", 409)
				return
			}
			sess, _ := s.sessions.Get(infos[0].ID)
			if sess.client.Store.ID == nil || sess.client.Store.ID.ToNonAD().String() != s.appGateway.cfg.Account {
				http.Error(w, "source_identity_mismatch", 403)
				return
			}
			r.SetPathValue("sid", infos[0].ID)
			r.SetPathValue("id", match[1])
			r.SetPathValue("applicationCallID", match[2])
			s.handleStream(w, r)
			return
		}
		http.NotFound(w, r)
	})
}
