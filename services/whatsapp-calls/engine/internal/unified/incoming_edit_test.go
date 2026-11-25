package unified

import (
	"bytes"
	"context"
	"encoding/json"
	"path/filepath"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"go.mau.fi/whatsmeow/util/gcmutil"
	"go.mau.fi/whatsmeow/util/hkdfutil"
	"google.golang.org/protobuf/proto"
)

type testEditSecret struct {
	store.MsgSecretStore
	chat types.JID
}

func (s testEditSecret) GetMessageSecret(_ context.Context, chat, sender types.JID, id string) ([]byte, types.JID, error) {
	if chat == s.chat && sender == chat && id == "CUSTOMER_MESSAGE" {
		return bytes.Repeat([]byte{63}, 32), sender, nil
	}
	return nil, types.EmptyJID, nil
}

func TestCaptureCustomerSecretEdit(t *testing.T) {
	ctx := context.Background()
	j := journal(t, database(t, filepath.Join(t.TempDir(), "journal.db")), testScope)
	customer := types.NewJID("123456789", types.HiddenUserServer)
	device := &store.Device{LID: types.NewJID("987654321", types.HiddenUserServer), MsgSecrets: testEditSecret{chat: customer}}
	client := &whatsmeow.Client{Store: device}
	key := &waCommon.MessageKey{RemoteJID: proto.String(device.LID.String()), FromMe: proto.Bool(true), ID: proto.String("CUSTOMER_MESSAGE")}
	change := &waE2E.ProtocolMessage{Type: waE2E.ProtocolMessage_MESSAGE_EDIT.Enum(), Key: key,
		TimestampMS: proto.Int64(1700000000123), EditedMessage: &waE2E.Message{Conversation: proto.String("Corrected text")}}
	wire, err := proto.Marshal(&waE2E.Message{ProtocolMessage: change, MessageContextInfo: &waE2E.MessageContextInfo{}})
	if err != nil {
		t.Fatal(err)
	}
	iv := bytes.Repeat([]byte{12}, 12)
	info := key.GetID() + customer.String() + customer.String() + "Message Edit"
	ciphertext, err := gcmutil.Encrypt(hkdfutil.SHA256(bytes.Repeat([]byte{63}, 32), nil, []byte(info), 32), iv, wire, nil)
	if err != nil {
		t.Fatal(err)
	}
	secret := &waE2E.SecretEncryptedMessage{SecretEncType: waE2E.SecretEncryptedMessage_MESSAGE_EDIT.Enum(),
		TargetMessageKey: key, EncIV: iv, EncPayload: ciphertext}
	event := &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: customer, Sender: customer},
		ID: "CORRECTION_EVENT", Timestamp: time.Unix(1700000000, 0)}, Message: &waE2E.Message{SecretEncryptedMessage: secret}}
	for i := 0; i < 2; i++ {
		if !j.Capture(ctx, event, client) {
			t.Fatal("capture failed")
		}
	}
	rows, err := j.Events(ctx, 0, 10)
	if err != nil || len(rows) != 1 {
		t.Fatal("provider retry did not converge", err)
	}
	var out Envelope
	if err = json.Unmarshal(rows[0].Payload, &out); err != nil || out.Variant != "control" || out.Edit == nil ||
		out.FromMe || out.Edit.ID != key.GetID() || out.Chat != customer.String() || out.Sender != customer.String() ||
		out.Timestamp != change.GetTimestampMS() || out.Text != change.GetEditedMessage().GetConversation() {
		t.Fatal("customer edit was not bound to original message and sender")
	}
	checkIgnored := func() {
		t.Helper()
		_, out, _, _ := MessageEnvelope(event)
		attachIncomingTextEdit(ctx, &out, event, client)
		if out.Edit != nil || out.Text != "" || out.Variant != "control" {
			t.Fatal("unverified control became a correction")
		}
	}
	secret.EncPayload[0] ^= 1
	checkIgnored()
	secret.EncIV = nil
	checkIgnored()
	event.Message = &waE2E.Message{ProtocolMessage: change}
	key.FromMe = proto.Bool(false)
	checkIgnored()
	key.FromMe = proto.Bool(true)
	event.Info.Chat = types.NewJID("123456789000", types.GroupServer)
	checkIgnored()
	key.RemoteJID = proto.String(event.Info.Chat.String())
	key.Participant = proto.String("55555555@lid")
	checkIgnored()
	key.Participant = proto.String(customer.String())
	_, group, _, _ := MessageEnvelope(event)
	attachIncomingTextEdit(ctx, &group, event, client)
	if group.Edit == nil || group.Chat != event.Info.Chat.String() || group.Sender != customer.String() {
		t.Fatal("group edit lost its authenticated author")
	}
}
