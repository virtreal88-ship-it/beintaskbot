package unified

import (
	"bytes"
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"

	_ "modernc.org/sqlite"
)

var testScope = Scope{Tenant: "tenant-a", Source: "source-a", Account: "15550000001@s.whatsapp.net", Generation: "test-generation-1"}
var testKey = bytes.Repeat([]byte{0x43}, 32)

func database(t *testing.T, path string) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite", "file:"+filepath.ToSlash(path)+"?_pragma=foreign_keys(1)&_pragma=journal_mode(WAL)&_pragma=busy_timeout(10000)")
	if err != nil {
		t.Fatal(err)
	}
	db.SetMaxOpenConns(1)
	t.Cleanup(func() { db.Close() })
	return db
}
func journal(t *testing.T, db *sql.DB, scope Scope) *Journal {
	t.Helper()
	j, err := New(context.Background(), db, scope, testKey)
	if err != nil {
		t.Fatal(err)
	}
	return j
}

func TestIsolationEncryptedEventsPaginationAndReplay(t *testing.T) {
	ctx := context.Background()
	db := database(t, filepath.Join(t.TempDir(), "journal.db"))
	a := journal(t, db, testScope)
	other := testScope
	other.Tenant = "tenant-b"
	b := journal(t, db, other)
	source := testScope
	source.Source = "source-b"
	c := journal(t, db, source)
	next := testScope
	next.Generation = "test-generation-2"
	d := journal(t, db, next)
	for _, j := range []*Journal{a, b, c, d} {
		if _, err := j.Append(ctx, "same-provider-id", []byte(`{"text":"private test words"}`)); err != nil {
			t.Fatal(err)
		}
		if _, err := j.Append(ctx, "same-provider-id", []byte(`{"text":"different redelivery metadata"}`)); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := a.Append(ctx, "second-provider-id", []byte(`{"text":"second"}`)); err != nil {
		t.Fatal(err)
	}
	first, err := a.Events(ctx, 0, 1)
	if err != nil || len(first) != 1 || !bytes.Contains(first[0].Payload, []byte("private test words")) {
		t.Fatalf("first page: %v %v", first, err)
	}
	second, err := a.Events(ctx, first[0].Sequence, 1)
	if err != nil || len(second) != 1 || bytes.Equal(first[0].Payload, second[0].Payload) {
		t.Fatal("cursor duplicated or skipped source event")
	}
	for _, j := range []*Journal{b, c, d} {
		items, err := j.Events(ctx, 0, 50)
		if err != nil || len(items) != 1 || items[0].ID == first[0].ID {
			t.Fatal("cross-scope collision")
		}
	}
	var body []byte
	if err := db.QueryRow(`SELECT body FROM unified_events WHERE scope=? ORDER BY sequence LIMIT 1`, a.scope).Scan(&body); err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(body, []byte("private test words")) {
		t.Fatal("plaintext persisted")
	}
	if _, err := New(ctx, db, testScope, bytes.Repeat([]byte{0x44}, 32)); !errors.Is(err, ErrConfiguration) {
		t.Fatal("wrong key accepted")
	}
	if _, err := db.Exec(`UPDATE unified_events SET body=? WHERE scope=?`, append([]byte(nil), body...), b.scope); err != nil {
		t.Fatal(err)
	}
	if _, err := b.Events(ctx, 0, 1); err == nil {
		t.Fatal("cross-scope ciphertext accepted")
	}
}

func TestConcurrentSendAndConflict(t *testing.T) {
	ctx := context.Background()
	j := journal(t, database(t, filepath.Join(t.TempDir(), "journal.db")), testScope)
	var count atomic.Int32
	gate := make(chan struct{})
	started := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		_, err := j.Send(ctx, "operation-a", "peer-a", []byte("same"), testProviderID, func(context.Context, string) error { count.Add(1); close(started); <-gate; return nil })
		done <- err
	}()
	<-started
	var workers sync.WaitGroup
	for i := 0; i < 20; i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			result, err := j.Send(ctx, "operation-a", "peer-a", []byte("same"), testProviderID, func(context.Context, string) error { count.Add(1); return nil })
			if !errors.Is(err, ErrUnknown) || result.State != "unknown" {
				t.Errorf("concurrent retry did not retain unknown: %v", err)
			}
		}()
	}
	workers.Wait()
	close(gate)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	result, err := j.Send(ctx, "operation-a", "peer-a", []byte("same"), testProviderID, func(context.Context, string) error { count.Add(1); return nil })
	if err != nil || result.State != "accepted" || count.Load() != 1 {
		t.Fatal("duplicate transport send")
	}
	for _, change := range []struct{ peer, body string }{{"peer-b", "same"}, {"peer-a", "changed"}} {
		_, err := j.Send(ctx, "operation-a", change.peer, []byte(change.body), testProviderID, func(context.Context, string) error { t.Fatal("conflicting send invoked"); return nil })
		if !errors.Is(err, ErrConflict) {
			t.Fatal("operation not immutable")
		}
	}
}

func TestSendAmbiguitySurvivesRestartAndCommitFailure(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "journal.db")
	db := database(t, path)
	j := journal(t, db, testScope)
	result, err := j.Send(ctx, "crash-after-provider", "peer", []byte("message"), testProviderID, func(context.Context, string) error { return db.Close() })
	if !errors.Is(err, ErrUnknown) || result.State != "unknown" {
		t.Fatal("failed local commit reported as success")
	}
	j = journal(t, database(t, path), testScope)
	_, err = j.Send(ctx, "crash-after-provider", "peer", []byte("message"), testProviderID, func(context.Context, string) error { t.Fatal("unknown send repeated after restart"); return nil })
	if !errors.Is(err, ErrUnknown) {
		t.Fatal(err)
	}
	_, err = j.Send(ctx, "provider-timeout", "peer", []byte("message"), testProviderID, func(context.Context, string) error { return errors.New("do not expose arbitrary provider error") })
	if !errors.Is(err, ErrUnknown) {
		t.Fatal(err)
	}
}

func TestConcurrentProcessesAndCrash(t *testing.T) {
	if os.Getenv("UNIFIED_TEST_CHILD") == "1" {
		j := journal(t, database(t, os.Getenv("UNIFIED_TEST_DB")), testScope)
		_, err := j.Send(context.Background(), "shared-operation", "peer", []byte("same"), testProviderID, func(context.Context, string) error {
			f, e := os.OpenFile(os.Getenv("UNIFIED_TEST_MARKER"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if e != nil {
				t.Fatal("a second process reached provider")
			}
			f.Close()
			os.Exit(0) // Abrupt process exit after durable reservation, before result.
			return nil
		})
		if !errors.Is(err, ErrUnknown) {
			t.Fatal(err)
		}
		return
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "journal.db")
	marker := filepath.Join(dir, "provider-called")
	journal(t, database(t, path), testScope)
	commands := make([]*exec.Cmd, 2)
	outputs := make([]bytes.Buffer, 2)
	for i := range commands {
		cmd := exec.Command(os.Args[0], "-test.run=^TestConcurrentProcessesAndCrash$")
		cmd.Env = append(os.Environ(), "UNIFIED_TEST_CHILD=1", "UNIFIED_TEST_DB="+path, "UNIFIED_TEST_MARKER="+marker)
		cmd.Stdout = &outputs[i]
		cmd.Stderr = &outputs[i]
		commands[i] = cmd
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
	}
	for i, cmd := range commands {
		if err := cmd.Wait(); err != nil {
			t.Fatalf("child %d: %v %s", i, err, outputs[i].String())
		}
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatal("no provider attempt occurred")
	}
}

func testProviderID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b[:])
}
