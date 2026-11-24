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
	ErrConfiguration    = errors.New("unified_configuration_invalid")
	ErrConflict         = errors.New("unified_operation_conflict")
	ErrQuoteUnavailable = errors.New("unified_quote_source_unverified")
	ErrUnknown          = errors.New("unified_send_outcome_unknown")
	ErrCapacity         = errors.New("unified_journal_capacity")
)

type Scope struct {
	Tenant     string `json:"tenant"`
	Source     string `json:"source"`
	Account    string `json:"account"`
	Generation string `json:"generation"`
}

type Journal struct {
	db             *sql.DB
	binding        Scope
	aead           cipher.AEAD
	key            []byte
	scope          string
	operationScope string
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
	stableScope := scope
	stableScope.Generation = ""
	stable, _ := json.Marshal(stableScope)
	stableHash := sha256.Sum256(stable)
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	j := &Journal{db: db, binding: scope, aead: aead, key: append([]byte(nil), key...), scope: hex.EncodeToString(hash[:]), operationScope: hex.EncodeToString(stableHash[:])}
	_, err = db.ExecContext(ctx, `
CREATE TABLE IF NOT EXISTS unified_bindings(scope TEXT PRIMARY KEY, key_check TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS unified_events(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, event_id TEXT NOT NULL,
 body BLOB NOT NULL, UNIQUE(scope,event_id));
CREATE INDEX IF NOT EXISTS unified_events_pending ON unified_events(scope,sequence);
CREATE TABLE IF NOT EXISTS unified_sends(
 scope TEXT NOT NULL, operation TEXT NOT NULL, fingerprint TEXT NOT NULL,
 peer_hash TEXT NOT NULL DEFAULT '', provider_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('unknown','accepted')),
 PRIMARY KEY(scope,operation), UNIQUE(scope,provider_id));
CREATE TABLE IF NOT EXISTS unified_consumers(
 scope TEXT NOT NULL, consumer TEXT NOT NULL, sequence INTEGER NOT NULL DEFAULT 0 CHECK(sequence>=0),
 PRIMARY KEY(scope,consumer));`)
	if err != nil {
		return nil, err
	}
	// Upgrade old durable sends without inventing their destination. Legacy
	// rows with no peer evidence remain unavailable to the new lookup.
	rows, err := db.QueryContext(ctx, "PRAGMA table_info(unified_sends)")
	if err != nil {
		return nil, err
	}
	hasPeer := false
	for rows.Next() {
		var cid, notNull, pk int
		var name, kind string
		var defaultValue any
		if err = rows.Scan(&cid, &name, &kind, &notNull, &defaultValue, &pk); err != nil {
			rows.Close()
			return nil, err
		}
		if name == "peer_hash" {
			hasPeer = true
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	if !hasPeer {
		if _, err = db.ExecContext(ctx, "ALTER TABLE unified_sends ADD COLUMN peer_hash TEXT NOT NULL DEFAULT ''"); err != nil {
			return nil, err
		}
	}
	for _, namespace := range []string{j.operationScope, j.scope} {
		check := j.digestForScope(namespace, "key-check")
		if _, err = db.ExecContext(ctx, `INSERT INTO unified_bindings(scope,key_check) VALUES(?,?) ON CONFLICT DO NOTHING`, namespace, check); err != nil {
			return nil, err
		}
		var stored string
		if err = db.QueryRowContext(ctx, `SELECT key_check FROM unified_bindings WHERE scope=?`, namespace).Scan(&stored); err != nil {
			return nil, err
		}
		if !hmac.Equal([]byte(stored), []byte(check)) {
			return nil, ErrConfiguration
		}
	}
	return j, nil
}

func (j *Journal) digest(parts ...string) string {
	return j.digestForScope(j.scope, parts...)
}

func (j *Journal) digestForScope(scope string, parts ...string) string {
	h := hmac.New(sha256.New, j.key)
	b, _ := json.Marshal(append([]string{scope}, parts...))
	h.Write(b)
	return hex.EncodeToString(h.Sum(nil))
}

func (j *Journal) seal(label string, payload []byte) ([]byte, error) {
	return j.sealForScope(j.scope, label, payload)
}

func (j *Journal) sealForScope(scope, label string, payload []byte) ([]byte, error) {
	nonce := make([]byte, j.aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return j.aead.Seal(nonce, nonce, payload, []byte(scope+":"+label)), nil
}

func (j *Journal) open(label string, body []byte) ([]byte, error) {
	return j.openForScope(j.scope, label, body)
}

func (j *Journal) openForScope(scope, label string, body []byte) ([]byte, error) {
	n := j.aead.NonceSize()
	if len(body) < n {
		return nil, ErrConfiguration
	}
	return j.aead.Open(nil, body[:n], body[n:], []byte(scope+":"+label))
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
	// Bound outstanding delivery, not lifetime history. ConfirmStored is the only
	// writer of this cursor. Retain encrypted events for media and replay dedup;
	// neither an attempted HTTP delivery nor a browser read frees queue capacity.
	result, err := j.db.ExecContext(ctx, `INSERT INTO unified_events(scope,event_id,body)
 SELECT ?,?,? WHERE (SELECT COUNT(*) FROM unified_events WHERE scope=? AND sequence >
 COALESCE((SELECT sequence FROM unified_consumers WHERE scope=? AND consumer=?),0)) < 10000
 ON CONFLICT(scope,event_id) DO NOTHING`, j.scope, id, body, j.scope, j.scope, WorkerInbox)
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

func (j *Journal) Event(ctx context.Context, id string) (Event, error) {
	if len(id) != 64 {
		return Event{}, sql.ErrNoRows
	}
	if _, err := hex.DecodeString(id); err != nil {
		return Event{}, sql.ErrNoRows
	}
	var event Event
	var body []byte
	err := j.db.QueryRowContext(ctx, `SELECT sequence,event_id,body FROM unified_events WHERE scope=? AND event_id=?`, j.scope, id).Scan(&event.Sequence, &event.ID, &body)
	if err != nil {
		return Event{}, err
	}
	event.Payload, err = j.open("event:"+event.ID, body)
	if err != nil {
		return Event{}, ErrConfiguration
	}
	return event, nil
}

// Send reserves the operation BEFORE invoking the transport exactly once. An
// error/crash/timeout never makes it retryable. Reconciliation can be added only
// with a matching provider receipt, not an elapsed timer or a text match.
func (j *Journal) Send(ctx context.Context, operation, peer string, payload []byte, allocateID func() string, send func(context.Context, string) error) (Result, error) {
	if operation == "" || len(operation) > 128 || peer == "" || len(payload) == 0 || len(payload) > 1<<20 || send == nil || allocateID == nil {
		return Result{}, ErrConfiguration
	}
	// Operation identity survives an owner-generation change. A new owner must
	// not retry an old unknown send with a new provider ID.
	op := j.digestForScope(j.operationScope, "operation", operation)
	fingerprint := j.digestForScope(j.operationScope, "payload", peer, string(payload))
	providerID := allocateID()
	if len(providerID) < 1 || len(providerID) > 200 {
		return Result{}, ErrConfiguration
	}
	result, err := j.db.ExecContext(ctx, `INSERT INTO unified_sends(scope,operation,fingerprint,peer_hash,provider_id,state) VALUES(?,?,?,?,?,'unknown') ON CONFLICT DO NOTHING`, j.operationScope, op, fingerprint, j.digestForScope(j.operationScope, "peer", peer), providerID)
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
		if err := j.db.QueryRowContext(ctx, `SELECT fingerprint,provider_id,state FROM unified_sends WHERE scope=? AND operation=?`, j.operationScope, op).Scan(&stored, &old.ProviderID, &old.State); err != nil {
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
	result, err = j.db.ExecContext(ctx, `UPDATE unified_sends SET state='accepted' WHERE scope=? AND operation=? AND fingerprint=? AND provider_id=? AND state='unknown'`, j.operationScope, op, fingerprint, providerID)
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
	return j.sealForScope(j.operationScope, fmt.Sprintf("buffer:%s:%s", kind, identity), payload)
}
func (j *Journal) BufferOpen(kind, identity string, body []byte) ([]byte, error) {
	return j.openForScope(j.operationScope, fmt.Sprintf("buffer:%s:%s", kind, identity), body)
}

// A quote refusal is proof of no send only when the operation was never reserved.
func (j *Journal) rejectQuote(ctx context.Context, operation string) error {
	var reserved bool
	err := j.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM unified_sends WHERE scope=? AND operation=?)`,
		j.operationScope, j.digestForScope(j.operationScope, "operation", operation)).Scan(&reserved)
	if err != nil {
		return err
	}
	if reserved {
		return ErrConflict
	}
	return ErrQuoteUnavailable
}

// Lookup never connects or dispatches. Exact operation, source and peer only.
func (j *Journal) Lookup(ctx context.Context, operation, peer string) (Result, error) {
	if operation == "" || len(operation) > 128 || !messagePeer.MatchString(peer) {
		return Result{}, ErrConfiguration
	}
	var result Result
	err := j.db.QueryRowContext(ctx, `SELECT provider_id,state FROM unified_sends WHERE scope=? AND operation=? AND peer_hash=?`,
		j.operationScope, j.digestForScope(j.operationScope, "operation", operation), j.digestForScope(j.operationScope, "peer", peer)).Scan(&result.ProviderID, &result.State)
	return result, err
}
