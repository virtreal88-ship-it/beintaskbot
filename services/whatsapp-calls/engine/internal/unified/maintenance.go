package unified

import "context"

// Maintenance contains counts only, never messages, addresses or credentials.
// The operator must fence Worker admission before using it for a rollback.
type Maintenance struct {
	UnknownSends  int64 `json:"unknown_sends"`
	PendingEvents int64 `json:"pending_events"`
}

func (j *Journal) Maintenance(ctx context.Context) (Maintenance, error) {
	var out Maintenance
	err := j.db.QueryRowContext(ctx, `SELECT
 (SELECT count(*) FROM unified_sends WHERE scope=? AND state='unknown'),
 (SELECT count(*) FROM unified_events WHERE scope=? AND sequence>COALESCE(
   (SELECT sequence FROM unified_consumers WHERE scope=? AND consumer=?),0))`,
		j.operationScope, j.scope, j.scope, WorkerInbox).Scan(&out.UnknownSends, &out.PendingEvents)
	return out, err
}
