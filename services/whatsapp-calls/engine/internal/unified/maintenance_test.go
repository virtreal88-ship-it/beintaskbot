package unified

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
)

func TestMaintenanceRetainsUnknownAndCountsOnlyBoundSource(t *testing.T) {
	ctx := context.Background()
	db := database(t, filepath.Join(t.TempDir(), "journal.db"))
	a := journal(t, db, testScope)
	scope := testScope
	scope.Source = "source-other"
	b := journal(t, db, scope)
	if _, err := a.Append(ctx, "event", []byte(`{"kind":"synthetic"}`)); err != nil {
		t.Fatal(err)
	}
	_, _ = a.Send(ctx, "unknown", "peer", []byte("synthetic"), func() string { return "PROVIDER_UNKNOWN" }, func(context.Context, string) error { return errors.New("synthetic timeout") })
	counts, err := a.Maintenance(ctx)
	if err != nil || counts.UnknownSends != 1 || counts.PendingEvents != 1 {
		t.Fatal(counts, err)
	}
	other, err := b.Maintenance(ctx)
	if err != nil || other.UnknownSends != 0 || other.PendingEvents != 0 {
		t.Fatal(other, err)
	}
	next, err := a.NextDelivery(ctx)
	if err != nil || next == nil {
		t.Fatal(err)
	}
	if err = a.ConfirmStored(ctx, StoredReceipt{Version: 1, Scope: testScope, After: next.After, Sequence: next.Event.Sequence, EventID: next.Event.ID, Proof: next.Proof, State: "stored"}); err != nil {
		t.Fatal(err)
	}
	counts, err = a.Maintenance(ctx)
	if err != nil || counts.UnknownSends != 1 || counts.PendingEvents != 0 {
		t.Fatal(counts, err)
	}
	if _, err = a.Event(ctx, next.Event.ID); err != nil {
		t.Fatal("acknowledged history lost", err)
	}
}
