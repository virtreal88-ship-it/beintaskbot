package main

// The gateway initiates HTTPS only. It does not expose the lab HTTP server,
// credentials, QR pairing or arbitrary URLs to Beintaskbot or its browsers.
import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"

	"go.mau.fi/whatsmeow/types"
	"wacalls/internal/voip/core"
	"wacalls/internal/voip/signaling"
	"wacalls/internal/wa"
)

type appGatewayConfig struct {
	Managed  bool     `json:"managed"`
	Endpoint string   `json:"endpoint"`
	Token    string   `json:"token"`
	Session  string   `json:"session_id"`
	Account  string   `json:"account_jid"`
	Peers    []string `json:"allowed_peers"`
}
type appCommand struct {
	ID         string `json:"id"`
	CallID     string `json:"call_id"`
	Op         string `json:"op"`
	ProviderID string `json:"provider_id"`
	Peer       string `json:"peer_jid"`
	Payload    struct {
		Media   string `json:"media"`
		SDP     string `json:"sdp_offer"`
		Consent bool   `json:"consent_confirmed"`
	} `json:"payload"`
	Cancelled bool `json:"cancelled"`
}
type appResult struct {
	ID         string `json:"id"`
	OK         bool   `json:"ok"`
	ProviderID string `json:"provider_id,omitempty"`
	SDP        string `json:"sdp_answer,omitempty"`
	Error      string `json:"error_code,omitempty"`
	NotStarted bool   `json:"not_started,omitempty"`
}
type appGateway struct {
	server  *server
	cfg     appGatewayConfig
	boot    string
	seq     int64
	client  *http.Client
	mu      sync.Mutex
	retired []string
}

func newAppGateway(s *server, path string) (*appGateway, error) {
	if path == "" {
		return nil, nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, errors.New("gateway_config_unavailable")
	}
	var c appGatewayConfig
	if json.Unmarshal(raw, &c) != nil {
		return nil, errors.New("gateway_config_invalid")
	}
	u, err := url.Parse(c.Endpoint)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "/api/platform/call-gateway" || len(c.Token) < 32 || len(c.Token) > 256 || (!c.Managed && (len(c.Peers) < 1 || len(c.Peers) > 5 || c.Session == "")) || s.recordings == nil || s.recordings.cfg.Account != c.Account || s.recordings.cfg.Managed != c.Managed {
		return nil, errors.New("gateway_config_invalid")
	}
	for _, peer := range c.Peers {
		found := false
		for _, p := range s.recordings.cfg.Peers {
			if p == peer {
				found = true
			}
		}
		if !found {
			return nil, errors.New("gateway_peer_not_recordable")
		}
	}
	_, err = s.sessions.store.db.Exec(`CREATE TABLE IF NOT EXISTS app_commands (id TEXT PRIMARY KEY, request TEXT NOT NULL, state TEXT NOT NULL, result BLOB)`)
	if err != nil {
		return nil, err
	}
	g := &appGateway{server: s, cfg: c, boot: randomUUID(), client: &http.Client{Timeout: 12 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("gateway_redirect_denied") }}}
	if c.Managed {
		if !s.runtimeExclusive {
			return nil, errors.New("runtime_fence_required")
		}
		if _, err = s.sessions.store.db.Exec(`CREATE TABLE IF NOT EXISTS app_runtime (boot_id TEXT PRIMARY KEY, opened_at INTEGER NOT NULL)`); err != nil {
			return nil, err
		}
		rows, e := s.sessions.store.db.Query(`SELECT boot_id FROM app_runtime ORDER BY opened_at DESC LIMIT 16`)
		if e != nil {
			return nil, e
		}
		for rows.Next() {
			var boot string
			if e = rows.Scan(&boot); e != nil {
				rows.Close()
				return nil, e
			}
			g.retired = append(g.retired, boot)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return nil, err
		}
		if _, err = s.sessions.store.db.Exec(`INSERT INTO app_runtime(boot_id,opened_at) VALUES(?,?)`, g.boot, time.Now().UnixMilli()); err != nil {
			return nil, err
		}
	}
	return g, nil
}
func randomUUID() string {
	x := newSessionID()
	return x[:8] + "-" + x[8:12] + "-" + x[12:16] + "-" + x[16:20] + "-" + x[20:]
}
func (g *appGateway) allowed(peer string) bool {
	if g.cfg.Managed {
		return directPeer(peer)
	}
	for _, p := range g.cfg.Peers {
		if p == peer {
			return true
		}
	}
	return false
}
func (g *appGateway) request(ctx context.Context, path, mime string, data []byte) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, "POST", g.cfg.Endpoint+path, bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+g.cfg.Token)
	req.Header.Set("Content-Type", mime)
	response, err := g.client.Do(req)
	if err != nil {
		return nil, errors.New("gateway_unreachable")
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, 150000))
	if err != nil || response.StatusCode != 200 {
		return nil, errors.New("gateway_request_rejected")
	}
	return raw, nil
}
func (g *appGateway) snapshot(sess *Session) map[string]any {
	active := []map[string]any{}
	ended := []map[string]any{}
	g.server.broker.mu.RLock()
	for _, r := range g.server.broker.calls {
		if r.SessionID == g.cfg.Session && g.allowed(r.Peer) {
			state := string(r.Status)
			if state == "starting" {
				state = "ringing"
			}
			active = append(active, map[string]any{"provider_id": r.CallID, "peer_jid": r.Peer, "state": state, "direction": r.Direction})
		}
	}
	g.server.broker.mu.RUnlock()
	for _, r := range g.server.broker.historyRows(g.cfg.Session, 20) {
		if g.allowed(r.Peer) {
			ended = append(ended, map[string]any{"provider_id": r.CallID, "peer_jid": r.Peer, "state": "ended", "direction": r.Direction})
		}
	}
	info := sess.info()
	g.seq++
	result := map[string]any{"boot_id": g.boot, "seq": g.seq, "connected": info.State == "open" && sess.client.Store.ID != nil && sess.client.Store.ID.ToNonAD().String() == g.cfg.Account, "active": active, "ended": ended}
	if g.cfg.Managed {
		result["closed_process_boots"] = append([]string{}, g.retired...)
	}
	return result
}
func (g *appGateway) run(ctx context.Context) {
	if g == nil {
		return
	}
	lastOK := time.Now()
	results := []appResult{}
	timer := time.NewTicker(time.Second)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
		}
		if g.cfg.Managed && g.cfg.Session == "" {
			infos := g.server.sessions.infos()
			if len(infos) == 1 {
				g.cfg.Session = infos[0].ID
			}
		}
		sess, ok := g.server.sessions.Get(g.cfg.Session)
		if !ok {
			continue
		}
		snapshot := g.snapshot(sess)
		snapshot["results"] = results
		raw, _ := json.Marshal(snapshot)
		response, err := g.request(ctx, "/sync", "application/json", raw)
		if err != nil {
			if time.Since(lastOK) > 15*time.Second {
				sess.teardownAllCalls()
			}
			continue
		}
		var envelope struct {
			Result struct {
				Commands   []appCommand `json:"commands"`
				Recordings []struct {
					CallID     string `json:"call_id"`
					ProviderID string `json:"provider_id"`
					Saved      bool   `json:"saved"`
				} `json:"recordings"`
			} `json:"result"`
		}
		if json.Unmarshal(response, &envelope) != nil || len(envelope.Result.Commands) > 1 {
			continue
		}
		lastOK = time.Now()
		results = []appResult{}
		for _, command := range envelope.Result.Commands {
			results = append(results, g.execute(ctx, sess, command))
		}
		if sess.reg.count() != 0 || len(envelope.Result.Commands) != 0 {
			continue
		}
		for _, item := range envelope.Result.Recordings {
			if item.Saved {
				continue
			}
			data, e := g.bundle(item.ProviderID)
			if e != nil {
				continue
			}
			_, _ = g.request(ctx, "/recording?call_id="+url.QueryEscape(item.CallID), "application/zip", data)
			break // bounded upload work; sync/termination remains responsive
		}
	}
}
func (g *appGateway) bundle(provider string) ([]byte, error) {
	store := g.server.recordings
	var id string
	var encrypted []byte
	if err := store.db.QueryRow("SELECT id,encrypted FROM recordings WHERE session_id=? AND call_id=? AND state IN ('ready','incomplete') AND expires_at>?", g.cfg.Session, provider, time.Now().UnixMilli()).Scan(&id, &encrypted); err != nil {
		return nil, err
	}
	n := store.aead.NonceSize()
	if len(encrypted) <= n {
		return nil, errors.New("recording_unavailable")
	}
	return store.aead.Open(nil, encrypted[:n], encrypted[n:], []byte(id))
}
func (g *appGateway) execute(ctx context.Context, sess *Session, cmd appCommand) appResult {
	g.mu.Lock()
	defer g.mu.Unlock()
	failed := appResult{ID: cmd.ID, Error: "scope_denied", NotStarted: cmd.Op == "start"}
	if len(cmd.ID) != 36 || len(cmd.CallID) != 36 || !g.allowed(cmd.Peer) || sess.client.Store.ID == nil || sess.client.Store.ID.ToNonAD().String() != g.cfg.Account {
		return failed
	}
	canonical := cmd
	canonical.Cancelled = false
	request, _ := json.Marshal(canonical)
	digest := sha256.Sum256(request)
	fingerprint := hex.EncodeToString(digest[:])
	var prior string
	var state string
	var result []byte
	err := g.server.sessions.store.db.QueryRow("SELECT request,state,result FROM app_commands WHERE id=?", cmd.ID).Scan(&prior, &state, &result)
	if err == nil {
		if prior != fingerprint {
			return failed
		}
		var saved appResult
		if state == "done" && json.Unmarshal(result, &saved) == nil {
			return saved
		}
		return appResult{ID: cmd.ID, Error: "command_uncertain"}
	}
	if _, err = g.server.sessions.store.db.Exec("INSERT INTO app_commands(id,request,state) VALUES(?,?,'executing')", cmd.ID, fingerprint); err != nil {
		return appResult{ID: cmd.ID, Error: "command_uncertain"}
	}
	out := g.perform(ctx, sess, cmd)
	encoded, _ := json.Marshal(out)
	if _, err = g.server.sessions.store.db.Exec("UPDATE app_commands SET state='done',result=? WHERE id=? AND state='executing'", encoded, cmd.ID); err != nil {
		return appResult{ID: cmd.ID, Error: "command_uncertain", ProviderID: out.ProviderID}
	}
	return out
}
func (g *appGateway) perform(ctx context.Context, sess *Session, cmd appCommand) appResult {
	out := appResult{ID: cmd.ID, ProviderID: cmd.ProviderID}
	if cmd.Cancelled && cmd.Op == "start" {
		out.Error = "call_ended"
		out.NotStarted = true
		return out
	}
	owner := "app:" + cmd.CallID
	if cmd.Op == "end" || cmd.Op == "reject" || cmd.Cancelled {
		if cmd.ProviderID == "" {
			out.OK = true
			return out
		}
		rec, ok := g.server.broker.getCall(cmd.ProviderID)
		if !ok {
			out.OK = true
			return out
		}
		if rec.SessionID != sess.id || rec.Peer != cmd.Peer || (rec.Owner != nil && *rec.Owner != owner) {
			out.Error = "scope_denied"
			return out
		}
		if cmd.Op == "reject" {
			if ac, exists := sess.reg.get(cmd.ProviderID); exists {
				if err := ac.cm.RejectCall(ctx, cmd.ProviderID, core.EndCallReasonDeclined); err != nil {
					out.Error = "call_ended"
					return out
				}
			}
		} else {
			sess.terminateCall(cmd.ProviderID, core.EndCallReasonUserEnded)
		}
		out.OK = true
		return out
	}
	validMedia := !g.cfg.Managed && strings.HasPrefix(cmd.Payload.SDP, "v=0") && len(cmd.Payload.SDP) <= 24000 || g.cfg.Managed && cmd.Payload.Media == "websocket"
	if (cmd.Op != "start" && cmd.Op != "accept") || !cmd.Payload.Consent || !validMedia {
		out.Error = "scope_denied"
		out.NotStarted = cmd.Op == "start"
		return out
	}
	if cmd.Op == "start" {
		peer, parseErr := types.ParseJID(cmd.Peer)
		resolveCtx, resolveCancel := context.WithTimeout(ctx, 5*time.Second)
		resolved := wa.NewSocket(sess.client).ResolveLIDForPN(resolveCtx, peer)
		resolveCancel()
		if parseErr != nil || !g.allowed(resolved.String()) {
			out.Error = "scope_denied"
			out.NotStarted = true
			return out
		}
		if sess.reg.count() != 0 {
			out.Error = "busy"
			out.NotStarted = true
			return out
		}
		id := signaling.GenerateCallID()
		cm := sess.createCall(id)
		if cm == nil {
			out.Error = "busy"
			out.NotStarted = true
			return out
		}
		out.ProviderID = id
		g.server.broker.upsertCall(CallRecord{SessionID: sess.id, CallID: id, Owner: &owner, Direction: "outbound", Peer: cmd.Peer, StartedAt: time.Now().UnixMilli(), Status: StatusStarting})
		ac, _ := sess.reg.get(id)
		h, err := g.server.recordings.begin(sess.id, id, g.cfg.Account, cmd.Peer)
		if err != nil || !sess.reg.attachRecording(id, ac, h) {
			if h != nil {
				h.finish(false)
			}
			sess.removeCall(id)
			g.server.broker.endCall(id, "recording_failed")
			out.Error = "recording_unavailable"
			return out
		}
		answer, err := g.makeBridge(sess, id, owner, cmd.Payload.SDP)
		if err != nil {
			sess.removeCall(id)
			g.server.broker.endCall(id, "bridge_failed")
			out.Error = "microphone_setup_failed"
			return out
		}
		callCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
		defer cancel()
		err = cm.StartCall(callCtx, id, resolved, false)
		if err != nil {
			ac.interrupted.Store(true)
			sess.terminateCall(id, core.EndCallReasonUserEnded)
			out.Error = "provider_start_failed"
			return out
		}
		out.OK = true
		out.SDP = answer
		return out
	}
	rec, ok := g.server.broker.getCall(cmd.ProviderID)
	if !ok || rec.SessionID != sess.id || rec.Peer != cmd.Peer || (rec.Owner != nil && *rec.Owner != owner) {
		out.Error = "call_ended"
		return out
	}
	if !g.server.broker.setOwner(cmd.ProviderID, owner) {
		out.Error = "busy"
		return out
	}
	answer, err := g.makeBridge(sess, cmd.ProviderID, owner, cmd.Payload.SDP)
	if err != nil {
		sess.terminateCall(cmd.ProviderID, core.EndCallReasonUserEnded)
		out.Error = "microphone_setup_failed"
		return out
	}
	payload := []byte(`{"record":true,"consent_confirmed":true}`)
	req := httptest.NewRequest("POST", "/", bytes.NewReader(payload))
	acceptCtx, acceptCancel := context.WithTimeout(ctx, 10*time.Second)
	defer acceptCancel()
	req = req.WithContext(acceptCtx)
	req.SetPathValue("id", cmd.ProviderID)
	req.Header.Set("X-Client-Id", owner)
	response := httptest.NewRecorder()
	g.server.doAccept(sess, response, req)
	if response.Code != 200 {
		out.Error = "recording_unavailable"
		return out
	}
	out.OK = true
	out.SDP = answer
	return out
}
func (g *appGateway) makeBridge(sess *Session, id, owner, sdp string) (string, error) {
	if g.cfg.Managed {
		ac, ok := sess.reg.get(id)
		if !ok {
			return "", errors.New("call_ended")
		}
		ac.setupMu.Lock()
		defer ac.setupMu.Unlock()
		if ac.bridge.Load() != nil {
			return "", errors.New("call_bridge_exists")
		}
		bridge := newStreamBridge(func(pcm []float32) {
			if current, ok := sess.reg.get(id); ok && current == ac {
				if h := ac.recording.Load(); h != nil {
					h.capture.Add(0, pcm, time.Now())
				}
				ac.cm.FeedCapturedPCM(pcm)
			}
		}, func() {
			go func() {
				if current, ok := sess.reg.get(id); ok && current == ac {
					ac.interrupted.Store(true)
					sess.terminateCall(id, core.EndCallReasonUserEnded)
				}
			}()
		})
		sess.setBridge(id, bridge)
		return "", nil
	}
	raw, _ := json.Marshal(map[string]string{"sdp_offer": sdp})
	req := httptest.NewRequest("POST", "/", bytes.NewReader(raw))
	req.SetPathValue("id", id)
	req.Header.Set("X-Client-Id", owner)
	response := httptest.NewRecorder()
	g.server.doWebRTC(sess, response, req)
	var result struct {
		Answer string `json:"sdp_answer"`
	}
	if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &result) != nil || result.Answer == "" {
		return "", errors.New("bridge_setup_failed")
	}
	return result.Answer, nil
}
