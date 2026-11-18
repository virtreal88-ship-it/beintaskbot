package unified

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"strconv"
)

// WorkerInbox is a protocol consumer, not a browser-selected destination. Adding
// a second consumer must be an explicit migration, not a request parameter.
const WorkerInbox = "worker-inbox-v1"

// Delivery contains exactly one retained event. After is the last confirmed
// cursor, not sequence-1: SQLite sequences are shared by independent scopes.
type Delivery struct {
	Version int    `json:"version"`
	Scope   Scope  `json:"scope"`
	After   int64  `json:"after"`
	Event   Event  `json:"event"`
	Proof   string `json:"proof"`
}

// StoredReceipt may be returned only after the authenticated Worker's inbox
// transaction commits. It means durable storage, NOT CRM projection, delivery
// to a customer, permission to send, or permission to delete the local event.
type StoredReceipt struct {
	Version  int    `json:"version"`
	Scope    Scope  `json:"scope"`
	After    int64  `json:"after"`
	Sequence int64  `json:"sequence"`
	EventID  string `json:"event_id"`
	Proof    string `json:"proof"`
	State    string `json:"state"`
}

func (j *Journal) deliveryProof(after int64, event Event) string {
	sum := sha256.Sum256(event.Payload)
	return j.digest("delivery", WorkerInbox, strconv.FormatInt(after, 10),
		strconv.FormatInt(event.Sequence, 10), event.ID, hex.EncodeToString(sum[:]))
}

// NextDelivery never advances a cursor. A timeout, lost reply or process restart
// offers the same immutable event again. The receiver must deduplicate by exact
// scope/event ID. No HTTP transport or production route is enabled here.
func (j *Journal) NextDelivery(ctx context.Context) (*Delivery, error) {
	if _, err := j.db.ExecContext(ctx, `INSERT INTO unified_consumers(scope,consumer,sequence)
 VALUES(?,?,0) ON CONFLICT DO NOTHING`, j.scope, WorkerInbox); err != nil {
		return nil, err
	}
	var after int64
	if err := j.db.QueryRowContext(ctx, `SELECT sequence FROM unified_consumers WHERE scope=? AND consumer=?`, j.scope, WorkerInbox).Scan(&after); err != nil {
		return nil, err
	}
	items, err := j.Events(ctx, after, 1)
	if err != nil || len(items) == 0 {
		return nil, err
	}
	event := items[0]
	return &Delivery{Version: 1, Scope: j.binding, After: after, Event: event, Proof: j.deliveryProof(after, event)}, nil
}

// ConfirmStored accepts only an exact receipt for the next offered event. The
// opaque proof is bound to payload and source generation; it is not a substitute
// for authenticating the Worker transport. A receipt from another generation
// cannot acknowledge this journal. Events are retained after acknowledgement.
func (j *Journal) ConfirmStored(ctx context.Context, receipt StoredReceipt) error {
	if receipt.Version != 1 || receipt.Scope != j.binding || receipt.State != "stored" ||
		receipt.After < 0 || receipt.Sequence <= receipt.After || len(receipt.Proof) != 64 {
		return ErrConflict
	}
	event, err := j.Event(ctx, receipt.EventID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return ErrConflict
		}
		return err
	}
	if event.Sequence != receipt.Sequence || !hmac.Equal([]byte(receipt.Proof), []byte(j.deliveryProof(receipt.After, event))) {
		return ErrConflict
	}
	first, err := j.Events(ctx, receipt.After, 1)
	if err != nil {
		return err
	}
	if len(first) != 1 || first[0].ID != receipt.EventID {
		return ErrConflict // Even a valid proof must not skip a retained event.
	}
	result, err := j.db.ExecContext(ctx, `UPDATE unified_consumers SET sequence=?
 WHERE scope=? AND consumer=? AND sequence=?`, receipt.Sequence, j.scope, WorkerInbox, receipt.After)
	if err != nil {
		return err
	}
	if n, err := result.RowsAffected(); err != nil {
		return err
	} else if n == 1 {
		return nil
	}
	var current int64
	if err := j.db.QueryRowContext(ctx, `SELECT sequence FROM unified_consumers WHERE scope=? AND consumer=?`, j.scope, WorkerInbox).Scan(&current); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return ErrConflict
		}
		return err
	}
	if current >= receipt.Sequence {
		return nil // Exact delayed/duplicate receipt: never move backward.
	}
	return ErrConflict
}
