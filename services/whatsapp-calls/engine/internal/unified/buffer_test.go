package unified

import (
	"bytes"
	"context"
	"path/filepath"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waAdv"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	waLog "go.mau.fi/whatsmeow/util/log"
)

type bufferFixture struct {
	store.EventBuffer
	event  *store.BufferedEvent
	out    []byte
	format string
}

func TestFreshPairingInstallsEncryptionBeforeSaveReturns(t *testing.T) {
	ctx := context.Background()
	db := database(t, filepath.Join(t.TempDir(), "pairing.db"))
	container := sqlstore.NewWithDB(db, "sqlite3", waLog.Noop)
	if err := container.Upgrade(ctx); err != nil {
		t.Fatal(err)
	}
	j := journal(t, db, testScope)
	device := container.NewDevice()
	ProtectDevice(device, j, testScope.Account)
	jid, _ := types.ParseJID(testScope.Account)
	device.ID = &jid
	device.Account = &waAdv.ADVSignedDeviceIdentity{Details: []byte{}, AccountSignature: make([]byte, 64), AccountSignatureKey: make([]byte, 32), DeviceSignature: make([]byte, 64)}
	if err := device.Save(ctx); err != nil {
		t.Fatal(err)
	}
	if _, ok := device.EventBuffer.(EncryptedBuffer); !ok {
		t.Fatal("pairing replaced encrypted retry buffer")
	}
	if err := device.EventBuffer.PutBufferedEvent(ctx, [32]byte{7}, []byte("fresh pairing plaintext"), time.Now()); err != nil {
		t.Fatal(err)
	}
	var raw []byte
	if err := db.QueryRow(`SELECT plaintext FROM whatsmeow_event_buffer`).Scan(&raw); err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(raw, []byte("fresh pairing plaintext")) {
		t.Fatal("first message leaked plaintext")
	}
	other := container.NewDevice()
	ProtectDevice(other, j, testScope.Account)
	wrong, _ := types.ParseJID("15550000099@s.whatsapp.net")
	other.ID = &wrong
	other.Account = &waAdv.ADVSignedDeviceIdentity{}
	if err := other.Save(ctx); err == nil {
		t.Fatal("QR for wrong account persisted")
	}
	var count int
	if err := db.QueryRow(`SELECT COUNT(*) FROM whatsmeow_device`).Scan(&count); err != nil || count != 1 {
		t.Fatal("wrong account changed device store")
	}
}

func (b *bufferFixture) GetBufferedEvent(context.Context, [32]byte) (*store.BufferedEvent, error) {
	return b.event, nil
}
func (b *bufferFixture) PutBufferedEvent(_ context.Context, _ [32]byte, body []byte, t time.Time) error {
	b.event = &store.BufferedEvent{Plaintext: body, ServerTime: t}
	return nil
}
func (b *bufferFixture) AddOutgoingEvent(_ context.Context, _ types.JID, _ string, format string, body []byte) error {
	b.out = body
	b.format = format
	return nil
}
func (b *bufferFixture) GetOutgoingEvent(context.Context, types.JID, types.JID, string) (string, []byte, error) {
	return b.format, b.out, nil
}

func TestProtocolBufferEncryptedAndBoundToSourceChatAndMessage(t *testing.T) {
	ctx := context.Background()
	j := journal(t, database(t, filepath.Join(t.TempDir(), "buffer.db")), testScope)
	base := &bufferFixture{}
	buffer := EncryptedBuffer{base, j}
	hash := [32]byte{1}
	plain := []byte("private protocol payload")
	if err := buffer.PutBufferedEvent(ctx, hash, plain, time.Now()); err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(base.event.Plaintext, plain) {
		t.Fatal("protocol buffer leaked plaintext")
	}
	first, err := buffer.GetBufferedEvent(ctx, hash)
	if err != nil || !bytes.Equal(first.Plaintext, plain) {
		t.Fatal("retry failed")
	}
	if bytes.Equal(base.event.Plaintext, plain) {
		t.Fatal("read mutated stored ciphertext")
	}
	if _, err := buffer.GetBufferedEvent(ctx, [32]byte{2}); err == nil {
		t.Fatal("ciphertext swapped across message IDs")
	}
	base.event.Plaintext = nil
	if got, err := buffer.GetBufferedEvent(ctx, hash); err != nil || got.Plaintext != nil {
		t.Fatal("processed tombstone lost")
	}
	chat, _ := types.ParseJID("15550000002@s.whatsapp.net")
	other, _ := types.ParseJID("15550000003@s.whatsapp.net")
	if err := buffer.AddOutgoingEvent(ctx, chat, "message-a", "protobuf", plain); err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(base.out, plain) {
		t.Fatal("outgoing retry body leaked plaintext")
	}
	if _, got, err := buffer.GetOutgoingEvent(ctx, chat, types.EmptyJID, "message-a"); err != nil || !bytes.Equal(got, plain) {
		t.Fatal("outgoing retry failed")
	}
	if _, _, err := buffer.GetOutgoingEvent(ctx, other, types.EmptyJID, "message-a"); err == nil {
		t.Fatal("outgoing body crossed chats")
	}
	if _, _, err := buffer.GetOutgoingEvent(ctx, chat, types.EmptyJID, "message-b"); err == nil {
		t.Fatal("outgoing body crossed IDs")
	}
}
