package unified

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"sync"
	"testing"
)

func stored(d *Delivery) StoredReceipt {
	return StoredReceipt{Version: d.Version, Scope: d.Scope, After: d.After, Sequence: d.Event.Sequence,
		EventID: d.Event.ID, Proof: d.Proof, State: "stored"}
}

func appendFixture(t *testing.T, j *Journal, id string) {
	t.Helper()
	if _, err := j.Append(context.Background(), id, []byte(`{"text":"synthetic"}`)); err != nil {
		t.Fatal(err)
	}
}

func next(t *testing.T, j *Journal) *Delivery {
	t.Helper()
	d, err := j.NextDelivery(context.Background())
	if err != nil || d == nil {
		t.Fatalf("expected next delivery: %v", err)
	}
	return d
}

func TestCommittedDeliveryWakesOneDrainerAndCoalesces(t *testing.T) {
	j := journal(t, database(t, filepath.Join(t.TempDir(), "journal.db")), testScope)
	appendFixture(t, j, "one")
	appendFixture(t, j, "two")
	select {
	case <-j.DeliveryReady():
	default:
		t.Fatal("a committed event did not wake delivery")
	}
	appendFixture(t, j, "one")
	select {
	case <-j.DeliveryReady():
		t.Fatal("a duplicate or queued notification caused another wakeup")
	default:
	}
	if next(t, j).Event.Sequence != 1 {
		t.Fatal("wakeups did not preserve journal order")
	}
}

func TestDeliveryLostReplyRestartAndNoDeletion(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "journal.db")
	db := database(t, path)
	j := journal(t, db, testScope)
	appendFixture(t, j, "one")
	appendFixture(t, j, "two")
	d := next(t, j)
	// Simulate the Worker committing, but the sender losing its response.
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	j = journal(t, database(t, path), testScope)
	retry := next(t, j)
	if retry.Event.ID != d.Event.ID || retry.Proof != d.Proof || retry.After != 0 {
		t.Fatal("lost acknowledgement skipped or mutated delivery")
	}
	if err := j.ConfirmStored(ctx, stored(retry)); err != nil {
		t.Fatal(err)
	}
	second := next(t, j)
	if second.Event.ID == d.Event.ID || second.After != d.Event.Sequence {
		t.Fatal("cursor not advanced")
	}
	if err := j.ConfirmStored(ctx, stored(second)); err != nil {
		t.Fatal(err)
	}
	if err := j.ConfirmStored(ctx, stored(d)); err != nil {
		t.Fatal("delayed exact receipt must be idempotent")
	}
	if done, err := j.NextDelivery(ctx); err != nil || done != nil {
		t.Fatal("cursor moved backward")
	}
	if items, err := j.Events(ctx, 0, 50); err != nil || len(items) != 2 {
		t.Fatal("acknowledgement deleted local events")
	}
}

func TestDeliveryReceiptCannotCrossScopeOrSkip(t *testing.T) {
	ctx := context.Background()
	db := database(t, filepath.Join(t.TempDir(), "journal.db"))
	j := journal(t, db, testScope)
	appendFixture(t, j, "one")
	d := next(t, j)
	for _, field := range []string{"tenant", "source", "account", "generation"} {
		scope := testScope
		switch field {
		case "tenant":
			scope.Tenant = "other"
		case "source":
			scope.Source = "other"
		case "account":
			scope.Account = "other"
		case "generation":
			scope.Generation = "other"
		}
		other := journal(t, db, scope)
		appendFixture(t, other, "one")
		_ = next(t, other)
		if !errors.Is(other.ConfirmStored(ctx, stored(d)), ErrConflict) {
			t.Fatalf("receipt crossed %s", field)
		}
	}
	appendFixture(t, j, "two")
	items, err := j.Events(ctx, 0, 50)
	if err != nil || len(items) != 2 {
		t.Fatal(err)
	}
	// A programming error in a transport must not skip the first event even if
	// that transport has obtained a valid proof for a later local event.
	skip := stored(d)
	skip.EventID, skip.Sequence = items[1].ID, items[1].Sequence
	skip.Proof = j.deliveryProof(0, items[1])
	if !errors.Is(j.ConfirmStored(ctx, skip), ErrConflict) {
		t.Fatal("skipped retained event")
	}
	for _, change := range []func(*StoredReceipt){
		func(r *StoredReceipt) { r.Version = 2 }, func(r *StoredReceipt) { r.State = "received" },
		func(r *StoredReceipt) { r.Proof = string(make([]byte, 64)) }, func(r *StoredReceipt) { r.After++ },
		func(r *StoredReceipt) { r.Sequence++ }, func(r *StoredReceipt) { r.EventID = "bad" },
	} {
		r := stored(d)
		change(&r)
		if !errors.Is(j.ConfirmStored(ctx, r), ErrConflict) {
			t.Fatal("invalid receipt advanced cursor")
		}
	}
	if next(t, j).Event.ID != d.Event.ID {
		t.Fatal("invalid ack advanced cursor")
	}
}

func TestDeliveryInterleavedSequencesAndConcurrentReceipts(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "journal.db")
	j := journal(t, database(t, path), testScope)
	otherScope := testScope
	otherScope.Tenant = "other"
	other := journal(t, database(t, path), otherScope)
	appendFixture(t, j, "one")
	appendFixture(t, other, "interleaved")
	appendFixture(t, j, "two")
	d := next(t, j)
	var wg sync.WaitGroup
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := j.ConfirmStored(ctx, stored(d)); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	second := next(t, j)
	if second.Event.Sequence != d.Event.Sequence+2 || second.After != d.Event.Sequence {
		t.Fatal("wrong scoped sequence")
	}
	if err := j.ConfirmStored(ctx, stored(second)); err != nil {
		t.Fatal(err)
	}
	if next(t, other).After != 0 {
		t.Fatal("other consumer advanced")
	}
	// A second DB connection/process observes the durable cursor, not memory.
	copy := journal(t, database(t, path), testScope)
	if d, err := copy.NextDelivery(ctx); err != nil || d != nil {
		t.Fatal("second connection lost cursor")
	}
}

func TestDeliveryStorageFailureAndChangedPayloadDoNotAcknowledge(t *testing.T) {
	ctx := context.Background()
	db := database(t, filepath.Join(t.TempDir(), "journal.db"))
	j := journal(t, db, testScope)
	appendFixture(t, j, "one")
	d := next(t, j)
	_, err := db.Exec(`CREATE TRIGGER fail_ack BEFORE UPDATE ON unified_consumers BEGIN SELECT RAISE(ABORT,'synthetic failure'); END`)
	if err != nil {
		t.Fatal(err)
	}
	if err = j.ConfirmStored(ctx, stored(d)); err == nil {
		t.Fatal("failed write reported success")
	}
	if next(t, j).Event.ID != d.Event.ID {
		t.Fatal("failed write skipped event")
	}
	if _, err = db.Exec(`DROP TRIGGER fail_ack`); err != nil {
		t.Fatal(err)
	}
	changed, err := j.seal("event:"+d.Event.ID, []byte(`{"text":"changed"}`))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`UPDATE unified_events SET body=? WHERE scope=? AND event_id=?`, changed, j.scope, d.Event.ID); err != nil {
		t.Fatal(err)
	}
	if !errors.Is(j.ConfirmStored(ctx, stored(d)), ErrConflict) {
		t.Fatal("receipt acknowledged changed payload")
	}
}

func TestCapacityCountsUnconfirmedNotLifetime(t *testing.T) {
	ctx := context.Background()
	j := journal(t, database(t, filepath.Join(t.TempDir(), "capacity.db")), testScope)
	// Fast fixture of retained, encrypted events with real source event IDs.
	tx, err := j.db.Begin()
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 10000; i++ {
		id := j.digest("event", fmt.Sprint(i))
		body, _ := j.seal("event:"+id, []byte(`{"text":"synthetic"}`))
		if _, err = tx.Exec(`INSERT INTO unified_events(scope,event_id,body) VALUES(?,?,?)`, j.scope, id, body); err != nil {
			t.Fatal(err)
		}
	}
	if err = tx.Commit(); err != nil {
		t.Fatal(err)
	}
	if _, err = j.Append(ctx, "new", []byte(`{}`)); !errors.Is(err, ErrCapacity) {
		t.Fatal("unconfirmed overflow", err)
	}
	d := next(t, j)
	bad := stored(d)
	bad.Proof = "0000000000000000000000000000000000000000000000000000000000000000"
	if err = j.ConfirmStored(ctx, bad); err == nil {
		t.Fatal("bad ACK accepted")
	}
	if _, err = j.Append(ctx, "new", []byte(`{}`)); !errors.Is(err, ErrCapacity) {
		t.Fatal("bad ACK freed space")
	}
	if err = j.ConfirmStored(ctx, stored(d)); err != nil {
		t.Fatal(err)
	}
	if _, err = j.Append(ctx, "new", []byte(`{}`)); err != nil {
		t.Fatal("delivered history still blocks", err)
	}
	if _, err = j.Event(ctx, d.Event.ID); err != nil {
		t.Fatal("media evidence removed")
	}
	if id, err := j.Append(ctx, "0", []byte(`{"changed":true}`)); err != nil || id != d.Event.ID {
		t.Fatal("old replay rejected", err)
	}
	var count int
	j.db.QueryRow(`SELECT COUNT(*) FROM unified_events WHERE scope=?`, j.scope).Scan(&count)
	if count != 10001 {
		t.Fatal("lost history or duplicate", count)
	}
}
