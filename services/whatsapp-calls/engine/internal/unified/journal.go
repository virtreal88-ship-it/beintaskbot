// Package unified contains the durable primitives for the isolated, opt-in
// messaging experiment. It is not a second CRM or an authorization service.
package unified

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
)

var (
	ErrConfiguration = errors.New("unified_configuration_invalid")
	ErrConflict      = errors.New("unified_operation_conflict")
	ErrUnknown       = errors.New("unified_send_outcome_unknown")
	ErrCapacity      = errors.New("unified_journal_capacity")
)

type Scope struct {
	Tenant     string `json:"tenant"`
	Source     string `json:"source"`
	Account    string `json:"account"`
	Generation string `json:"generation"`
}

type Journal struct {
	db    *sql.DB
	aead  cipher.AEAD
	key   []byte
	scope string
}

type Event struct {
	Sequence int64           `json:"sequence"`
	ID       string          `json:"id"`
	Payload  json.RawMessage `json:"payload"`
}

type Result struct {
	ProviderID string `json:"provider_id"`
	State      string `json:"state"` // accepted is server acceptance, not delivery/read.
}

// New binds a journal to an exact source and generation; callers cannot override
// scope in Append/Events/Send. A wrong encryption key fails instead of silently
// exposing an empty journal and permitting resends.
func New(ctx context.Context, db *sql.DB, scope Scope, key []byte) (*Journal, error) {
	if len(key) != 32 || scope.Tenant == "" || scope.Source == "" || scope.Account == "" || scope.Generation == "" {
		return nil, ErrConfiguration
	}
	var synchronous int
	if err := db.QueryRowContext(ctx, "PRAGMA synchronous").Scan(&synchronous); err != nil {
		return nil, err
	}
	if synchronous < 2 {
		return nil, ErrConfiguration
	}
	b, _ := json.Marshal(scope)
	hash := sha256.Sum256(b)
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	j := &Journal{db: db, aead: aead, key: append([]byte(nil), key...), scope: hex.EncodeToString(hash[:])}
	_, err = db.ExecContext(ctx, `
CREATE TABLE IF NOT EXISTS unified_bindings(scope TEXT PRIMARY KEY, key_check TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS unified_events(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, event_id TEXT NOT NULL,
 body BLOB NOT NULL, UNIQUE(scope,event_id));
CREATE TABLE IF NOT EXISTS unified_sends(
 scope TEXT NOT NULL, operation TEXT NOT NULL, fingerprint TEXT NOT NULL,
 provider_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('unknown','accepted')),
 PRIMARY KEY(scope,operation), UNIQUE(scope,provider_id));`)
	if err != nil {
		return nil, err
	}
	check := j.digest("key-check")
	if _, err = db.ExecContext(ctx, `INSERT INTO unified_bindings(scope,key_check) VALUES(?,?) ON CONFLICT DO NOTHING`, j.scope, check); err != nil {
		return nil, err
	}
	var stored string
	if err = db.QueryRowContext(ctx, `SELECT key_check FROM unified_bindings WHERE scope=?`, j.scope).Scan(&stored); err != nil {
		return nil, err
	}
	if !hmac.Equal([]byte(stored), []byte(check)) {
		return nil, ErrConfiguration
	}
	return j, nil
}

func (j *Journal) digest(parts ...string) string {
	h := hmac.New(sha256.New, j.key)
	b, _ := json.Marshal(append([]string{j.scope}, parts...))
	h.Write(b)
	return hex.EncodeToString(h.Sum(nil))
}

func (j *Journal) seal(label string, payload []byte) ([]byte, error) {
	nonce := make([]byte, j.aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return j.aead.Seal(nonce, nonce, payload, []byte(j.scope+":"+label)), nil
}

func (j *Journal) open(label string, body []byte) ([]byte, error) {
	n := j.aead.NonceSize()
	if len(body) < n {
		return nil, ErrConfiguration
	}
	return j.aead.Open(nil, body[:n], body[n:], []byte(j.scope+":"+label))
}

// Append commits before returning success to the provider callback. Identical
// source events retain the first payload. Metadata changes on a redelivery do
// not become new messages. Edits/reactions must have distinct event identities.
func (j *Journal) Append(ctx context.Context, identity string, payload []byte) (string, error) {
	if identity == "" || len(identity) > 4096 || len(payload) > 1<<20 || !json.Valid(payload) {
		return "", ErrConfiguration
	}
	id := j.digest("event", identity)
	body, err := j.seal("event:"+id, payload)
	if err != nil {
		return "", err
	}
	// Bound the lab journal. Existing IDs are still successful at capacity.
	result, err := j.db.ExecContext(ctx, `INSERT INTO unified_events(scope,event_id,body)
 SELECT ?,?,? WHERE (SELECT COUNT(*) FROM unified_events WHERE scope=?) < 10000
 ON CONFLICT(scope,event_id) DO NOTHING`, j.scope, id, body, j.scope)
	if err != nil {
		return "", err
	}
	if n, _ := result.RowsAffected(); n == 0 {
		var exists int
		if err := j.db.QueryRowContext(ctx, `SELECT 1 FROM unified_events WHERE scope=? AND event_id=?`, j.scope, id).Scan(&exists); err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				return "", ErrCapacity
			}
			return "", err
		}
	}
	return id, nil
}

func (j *Journal) Events(ctx context.Context, after int64, limit int) ([]Event, error) {
	if after < 0 || limit < 1 || limit > 50 {
		return nil, ErrConfiguration
	}
	rows, err := j.db.QueryContext(ctx, `SELECT sequence,event_id,body FROM unified_events WHERE scope=? AND sequence>? ORDER BY sequence LIMIT ?`, j.scope, after, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make([]Event, 0)
	for rows.Next() {
		var event Event
		var body []byte
		if err := rows.Scan(&event.Sequence, &event.ID, &body); err != nil {
			return nil, err
		}
		plain, err := j.open("event:"+event.ID, body)
		if err != nil {
			return nil, ErrConfiguration
		}
		event.Payload = plain
		out = append(out, event)
	}
	return out, rows.Err()
}

// Send reserves the operation BEFORE invoking the transport exactly once. An
// error/crash/timeout never makes it retryable. Reconciliation can be added only
// with a matching provider receipt, not an elapsed timer or a text match.
func (j *Journal) Send(ctx context.Context, operation, peer string, payload []byte, allocateID func() string, send func(context.Context, string) error) (Result, error) {
	if operation == "" || len(operation) > 128 || peer == "" || len(payload) == 0 || len(payload) > 1<<20 || send == nil || allocateID == nil {
		return Result{}, ErrConfiguration
	}
	op := j.digest("operation", operation)
	fingerprint := j.digest("payload", peer, string(payload))
	providerID := allocateID()
	if len(providerID) < 1 || len(providerID) > 200 {
		return Result{}, ErrConfiguration
	}
	result, err := j.db.ExecContext(ctx, `INSERT INTO unified_sends(scope,operation,fingerprint,provider_id,state) VALUES(?,?,?,?,'unknown') ON CONFLICT DO NOTHING`, j.scope, op, fingerprint, providerID)
	if err != nil {
		return Result{}, err
	}
	n, err := result.RowsAffected()
	if err != nil {
		return Result{}, err
	}
	if n == 0 {
		var stored string
		var old Result
		if err := j.db.QueryRowContext(ctx, `SELECT fingerprint,provider_id,state FROM unified_sends WHERE scope=? AND operation=?`, j.scope, op).Scan(&stored, &old.ProviderID, &old.State); err != nil {
			return Result{}, err
		}
		if !hmac.Equal([]byte(stored), []byte(fingerprint)) {
			return Result{}, ErrConflict
		}
		if old.State == "unknown" {
			return old, ErrUnknown
		}
		return old, nil
	}
	unknown := Result{ProviderID: providerID, State: "unknown"}
	if err := send(ctx, providerID); err != nil {
		return unknown, ErrUnknown
	}
	result, err = j.db.ExecContext(ctx, `UPDATE unified_sends SET state='accepted' WHERE scope=? AND operation=? AND fingerprint=? AND provider_id=? AND state='unknown'`, j.scope, op, fingerprint, providerID)
	if err != nil {
		return unknown, ErrUnknown
	}
	if n, err = result.RowsAffected(); err != nil || n != 1 {
		return unknown, ErrUnknown
	}
	return Result{ProviderID: providerID, State: "accepted"}, nil
}

// BufferSeal/BufferOpen bind the protocol retry buffer to this exact scope.
func (j *Journal) BufferSeal(kind, identity string, payload []byte) ([]byte, error) {
	return j.seal(fmt.Sprintf("buffer:%s:%s", kind, identity), payload)
}
func (j *Journal) BufferOpen(kind, identity string, body []byte) ([]byte, error) {
	return j.open(fmt.Sprintf("buffer:%s:%s", kind, identity), body)
}
