package main

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/ed25519"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"regexp"
	"runtime/debug"
	"sync"
	"time"

	"wacalls/internal/recording"
)

type recordingConfig struct {
	CapacityDir string   `json:"capacity_dir"`
	Managed     bool     `json:"managed"`
	Account     string   `json:"account_jid"`
	Peers       []string `json:"allowed_peers"`
	Seed        string   `json:"signing_seed"`
	Encryption  string   `json:"encryption_key"`
}

var directPeerPattern = regexp.MustCompile(`^[1-9][0-9]{5,21}@(s\.whatsapp\.net|lid)$`)

func directPeer(peer string) bool { return directPeerPattern.MatchString(peer) }

type recordingStore struct {
	db        *sql.DB
	mu        sync.Mutex
	cfg       recordingConfig
	seed      []byte
	aead      cipher.AEAD
	pendingMu sync.Mutex
	pending   map[string]pendingRecording
}
type pendingRecording struct {
	state     string
	encrypted []byte
	expires   time.Time
	slot      *os.File
}
type recordingHandle struct {
	id      string
	capture *recording.Capture
	store   *recordingStore
	once    sync.Once
	slot    *os.File
}
type recordingRow struct {
	ID        string `json:"id"`
	CallID    string `json:"callId"`
	State     string `json:"state"`
	CreatedAt int64  `json:"createdAt"`
	ExpiresAt int64  `json:"expiresAt"`
}

func newRecordingStore(ctx context.Context, db *sql.DB, configFile string) (*recordingStore, error) {
	if configFile == "" {
		return nil, nil
	}
	raw, e := os.ReadFile(configFile)
	if e != nil {
		return nil, errors.New("recording_config_unavailable")
	}
	var cfg recordingConfig
	if json.Unmarshal(raw, &cfg) != nil || cfg.Account == "" || (!cfg.Managed && (len(cfg.Peers) == 0 || len(cfg.Peers) > 5)) {
		return nil, errors.New("recording_config_invalid")
	}
	seed, e := hex.DecodeString(cfg.Seed)
	if e != nil || len(seed) != ed25519.SeedSize {
		return nil, errors.New("recording_key_invalid")
	}
	key, e := hex.DecodeString(cfg.Encryption)
	if e != nil || len(key) != 32 {
		return nil, errors.New("recording_key_invalid")
	}
	block, e := aes.NewCipher(key)
	if e != nil {
		return nil, e
	}
	aead, e := cipher.NewGCM(block)
	if e != nil {
		return nil, e
	}
	_, e = db.ExecContext(ctx, `CREATE TABLE IF NOT EXISTS recordings (
	 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, call_id TEXT NOT NULL,
	 state TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
	 encrypted BLOB, UNIQUE(session_id,call_id))`)
	if e != nil {
		return nil, e
	}
	// A process restart cannot turn an unfinished in-memory capture into success.
	if _, e = db.ExecContext(ctx, "UPDATE recordings SET state='interrupted' WHERE state='recording'"); e != nil {
		return nil, e
	}
	s := &recordingStore{db: db, cfg: cfg, seed: seed, aead: aead, pending: make(map[string]pendingRecording)}
	if e = s.purge(ctx); e != nil {
		return nil, e
	}
	go func() {
		t := time.NewTicker(5 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				s.flushPending()
				_ = s.purge(ctx)
			}
		}
	}()
	return s, nil
}
func (s *recordingStore) purge(ctx context.Context) error {
	_, e := s.db.ExecContext(ctx, "DELETE FROM recordings WHERE expires_at<=?", time.Now().UnixMilli())
	return e
}
func (s *recordingStore) begin(session, call, account, peer string) (*recordingHandle, error) {
	if s == nil {
		return nil, errors.New("recording_disabled")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	allowed := s.cfg.Managed && directPeer(peer)
	for _, p := range s.cfg.Peers {
		if p == peer {
			allowed = true
		}
	}
	if account != s.cfg.Account || !allowed {
		return nil, errors.New("recording_test_scope_required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if e := s.purge(ctx); e != nil {
		return nil, e
	}
	var count int
	if e := s.db.QueryRowContext(ctx, "SELECT count(*) FROM recordings").Scan(&count); e != nil {
		return nil, e
	}
	if count >= 20 {
		return nil, errors.New("recording_storage_limit")
	}
	slot, capacityErr := s.acquireWork(true)
	if capacityErr != nil {
		return nil, capacityErr
	}
	reserved := false
	defer func() {
		if !reserved {
			releaseWork(slot)
		}
	}()
	id := newSessionID()
	now := time.Now()
	_, e := s.db.ExecContext(ctx, "INSERT INTO recordings(id,session_id,call_id,state,created_at,expires_at) VALUES(?,?,?,'recording',?,?)", id, session, call, now.UnixMilli(), now.Add(24*time.Hour).UnixMilli())
	if e != nil {
		return nil, errors.New("recording_reservation_failed")
	}
	capture := recording.New(session, call, account, peer, now)
	if s.cfg.Managed {
		capture = recording.NewManaged(session, call, account, peer, now)
	}
	reserved = true
	return &recordingHandle{id: id, capture: capture, store: s, slot: slot}, nil
}
func (h *recordingHandle) finish(graceful bool) {
	if h == nil {
		return
	}
	h.once.Do(func() {
		defer debug.FreeOSMemory()
		data, manifest, e := h.capture.Finish(time.Now(), graceful, h.store.seed)
		state := "failed"
		var encrypted []byte
		if e == nil {
			nonce := make([]byte, h.store.aead.NonceSize())
			if _, e = rand.Read(nonce); e == nil {
				encrypted = h.store.aead.Seal(nonce, nonce, data, []byte(h.id))
				state = "incomplete"
				if manifest.Complete {
					state = "ready"
				}
			}
		}
		h.store.pendingMu.Lock()
		h.store.pending[h.id] = pendingRecording{state: state, encrypted: encrypted, expires: time.Now().Add(24 * time.Hour), slot: h.slot}
		h.store.pendingMu.Unlock()
		h.store.flushPending()
	})
}

// Keep only encrypted data while retrying a failed final write. Never report
// success before durable storage. A process crash is explicitly interrupted.
func (s *recordingStore) flushPending() {
	if s == nil {
		return
	}
	s.pendingMu.Lock()
	defer s.pendingMu.Unlock()
	for id, p := range s.pending {
		if time.Now().After(p.expires) {
			releaseWork(p.slot)
			delete(s.pending, id)
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		_, err := s.db.ExecContext(ctx, "UPDATE recordings SET state=?,encrypted=? WHERE id=? AND state='recording'", p.state, p.encrypted, id)
		cancel()
		if err == nil {
			releaseWork(p.slot)
			delete(s.pending, id)
		}
	}
}
func (s *server) handleRecordings(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if s.recordings == nil {
		writeJSON(w, 200, map[string]any{"rows": []any{}, "enabled": false})
		return
	}
	sid := r.PathValue("sid")
	if s.sessionByID(w, sid) == nil {
		return
	}
	rows, e := s.recordings.db.QueryContext(r.Context(), "SELECT id,call_id,state,created_at,expires_at FROM recordings WHERE session_id=? AND expires_at>? ORDER BY created_at DESC LIMIT 20", sid, time.Now().UnixMilli())
	if e != nil {
		http.Error(w, "recording_read_failed", 500)
		return
	}
	defer rows.Close()
	out := []recordingRow{}
	for rows.Next() {
		var x recordingRow
		if rows.Scan(&x.ID, &x.CallID, &x.State, &x.CreatedAt, &x.ExpiresAt) != nil {
			http.Error(w, "recording_read_failed", 500)
			return
		}
		out = append(out, x)
	}
	if rows.Err() != nil {
		http.Error(w, "recording_read_failed", 500)
		return
	}
	writeJSON(w, 200, map[string]any{"rows": out, "enabled": true})
}
func (s *server) handleRecordingDownload(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if s.recordings == nil {
		http.NotFound(w, r)
		return
	}
	sid := r.PathValue("sid")
	if s.sessionByID(w, sid) == nil {
		return
	}
	id := r.PathValue("rid")
	if len(id) != 32 {
		http.NotFound(w, r)
		return
	}
	var encrypted []byte
	e := s.recordings.db.QueryRowContext(r.Context(), "SELECT encrypted FROM recordings WHERE id=? AND session_id=? AND state IN ('ready','incomplete') AND expires_at>?", id, sid, time.Now().UnixMilli()).Scan(&encrypted)
	if e != nil || len(encrypted) < s.recordings.aead.NonceSize() {
		http.NotFound(w, r)
		return
	}
	n := s.recordings.aead.NonceSize()
	plain, e := s.recordings.aead.Open(nil, encrypted[:n], encrypted[n:], []byte(id))
	if e != nil {
		http.Error(w, "recording_integrity_failed", 500)
		return
	}
	if r.URL.Query().Get("format") == "wav" {
		z, e := zip.NewReader(bytes.NewReader(plain), int64(len(plain)))
		if e != nil {
			http.Error(w, "recording_integrity_failed", 500)
			return
		}
		for _, f := range z.File {
			if f.Name == "recording.wav" {
				reader, e := f.Open()
				if e != nil {
					http.Error(w, "recording_read_failed", 500)
					return
				}
				defer reader.Close()
				w.Header().Set("Content-Type", "audio/wav")
				w.Header().Set("Content-Disposition", `inline; filename="test-call.wav"`)
				_, _ = io.Copy(w, reader)
				return
			}
		}
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Content-Disposition", `attachment; filename="test-call.beincall.zip"`)
	_, _ = w.Write(plain)
}
