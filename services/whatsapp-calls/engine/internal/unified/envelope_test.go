package unified

import (
	"context"
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

func TestGroupSenderKeyAndContentWithSameProviderID(t *testing.T) {
	for _, reverse := range []bool{false, true} {
		for _, fast := range []bool{false, true} {
			j := journal(t, database(t, filepath.Join(t.TempDir(), "journal.db")), testScope)
			info := types.MessageInfo{MessageSource: types.MessageSource{
				Chat:   types.NewJID("120363000000001", types.GroupServer),
				Sender: types.NewJID("15550000002", types.DefaultUserServer), IsGroup: true},
				ID: "SAME_PROVIDER_ID", Timestamp: time.Unix(1700000000, 0)}
			keyMessage := &waE2E.Message{}
			key := &waE2E.SenderKeyDistributionMessage{GroupID: proto.String(info.Chat.String()), AxolotlSenderKeyDistributionMessage: []byte("synthetic key")}
			if fast {
				keyMessage.FastRatchetKeySenderKeyDistributionMessage = key
			} else {
				keyMessage.SenderKeyDistributionMessage = key
			}
			control := &events.Message{Info: info, Message: keyMessage}
			content := &events.Message{Info: info, Message: &waE2E.Message{Conversation: proto.String("GROUP CONTENT")}}
			ordered := []*events.Message{control, content}
			if reverse {
				ordered[0], ordered[1] = ordered[1], ordered[0]
			}
			for repeat := 0; repeat < 3; repeat++ {
				for _, e := range ordered {
					if !j.Capture(context.Background(), e, nil) {
						t.Fatal("capture failed")
					}
				}
			}
			rows, err := j.Events(context.Background(), 0, 10)
			if err != nil || len(rows) != 2 {
				t.Fatalf("key/content collision or replay duplication: count=%d err=%v", len(rows), err)
			}
			var controls, texts int
			for _, row := range rows {
				var envelope Envelope
				if json.Unmarshal(row.Payload, &envelope) != nil {
					t.Fatal("invalid envelope")
				}
				if envelope.Variant == "control" && envelope.MessageType == "unsupported" {
					controls++
				}
				if envelope.Variant == "" && envelope.MessageType == "text" && envelope.Text == "GROUP CONTENT" {
					texts++
				}
			}
			if controls != 1 || texts != 1 {
				t.Fatal("original content lost or key projected as text")
			}
			// A provider may instead include content and the key in one protobuf.
			combined := proto.Clone(keyMessage).(*waE2E.Message)
			combined.Conversation = proto.String("GROUP CONTENT")
			combinedID, combinedEnvelope, _, _ := MessageEnvelope(&events.Message{Info: info, Message: combined})
			contentID, _, _, _ := MessageEnvelope(content)
			if combinedID != contentID || combinedEnvelope.Variant != "" || combinedEnvelope.MessageType != "text" {
				t.Fatal("combined content lost its original identity")
			}
		}
	}
}

func TestMessageEnvelopePreservesIdentityAndExplicitProviderFacts(t *testing.T) {
	chat := types.NewJID("120363000000001", types.GroupServer)
	sender := types.NewJID("15550000002", types.DefaultUserServer)
	e := &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: chat, Sender: sender,
		SenderAlt: types.NewJID("123456789", types.HiddenUserServer)}, ID: "test-message", PushName: "Synthetic name",
		Timestamp: time.UnixMilli(1234567890000)}, Message: &waE2E.Message{ImageMessage: &waE2E.ImageMessage{
		Caption: proto.String("Synthetic caption"), Mimetype: proto.String("image/png"), MediaKey: []byte("PRIVATE_SYNTHETIC_KEY"),
		ContextInfo: &waE2E.ContextInfo{StanzaID: proto.String("quoted-message"), RemoteJID: proto.String(chat.String()), Participant: proto.String(sender.String())}}}}
	identity, out, retained, err := MessageEnvelope(e)
	if err != nil || !retained || identity != `["message","120363000000001@g.us","15550000002@s.whatsapp.net",false,"test-message","original"]` {
		t.Fatal("existing event identity changed")
	}
	if out.MessageType != "image" || out.Text != "Synthetic caption" || out.MediaKind != "image" || out.PushName != "Synthetic name" || out.QuoteID != "quoted-message" || out.SenderAlt != "123456789@lid" || out.Timestamp != 1234567890000 {
		t.Fatal("provider facts lost")
	}
	// This envelope is encrypted, not a UI DTO. Removing only Proto must remove
	// media credentials while leaving the explicitly selected visible metadata.
	out.Proto = nil
	visible, _ := json.Marshal(out)
	if strings.Contains(string(visible), "PRIVATE_SYNTHETIC_KEY") || strings.Contains(string(visible), "mediaKey") {
		t.Fatal("secret outside protocol envelope")
	}
	e.Info.PushName = "Renamed"
	again, _, _, _ := MessageEnvelope(e)
	if again != identity {
		t.Fatal("metadata update duplicated a message")
	}
	e.IsEdit = true
	edited, edit, _, _ := MessageEnvelope(e)
	if edited == identity || edit.Variant != "control" || edit.MessageType != "unsupported" {
		t.Fatal("edit projected as a fresh message")
	}
}

func TestMessageEnvelopeEphemeralAndMalformed(t *testing.T) {
	for _, e := range []*events.Message{nil, {}, {Message: &waE2E.Message{Conversation: proto.String("synthetic")}}} {
		if _, _, _, err := MessageEnvelope(e); err == nil {
			t.Fatal("malformed event admitted")
		}
	}
	for _, flag := range []func(*events.Message){
		func(e *events.Message) { e.IsEphemeral = true }, func(e *events.Message) { e.IsViewOnce = true },
		func(e *events.Message) { e.IsViewOnceV2 = true }, func(e *events.Message) { e.IsViewOnceV2Extension = true },
	} {
		e := &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: types.NewJID("15550000002", types.DefaultUserServer)}, ID: "test-message"}, Message: &waE2E.Message{Conversation: proto.String("do not retain")}}
		flag(e)
		if key, out, retained, err := MessageEnvelope(e); err != nil || retained || key != "" || len(out.Proto) != 0 || out.Text != "" {
			t.Fatal("expiring message retained")
		}
	}
}

func TestRevokeEnvelopeCarriesExactTargetAndIgnoresLocalDeleteForMe(t *testing.T) {
	chat := types.NewJID("120363000000001", types.GroupServer)
	participant := types.NewJID("15550000002", types.DefaultUserServer)
	message := &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{
		Type: waE2E.ProtocolMessage_REVOKE.Enum(),
		Key: &waCommon.MessageKey{RemoteJID: proto.String(chat.String()), ID: proto.String("TARGET_MESSAGE"),
			FromMe: proto.Bool(true)},
	}}
	event := &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: chat, Sender: participant},
		ID: "REVOKE_EVENT", Timestamp: time.Unix(1700000000, 0)}, Message: message}
	_, envelope, retained, err := MessageEnvelope(event)
	if err != nil || !retained || envelope.Variant != "control" || envelope.Revoke == nil ||
		envelope.Revoke.ID != "TARGET_MESSAGE" || envelope.Revoke.Chat != chat.String() ||
		envelope.Revoke.FromMe || envelope.Revoke.Participant != participant.String() {
		t.Fatalf("revoke target lost or became a chat message: %+v %v", envelope, err)
	}

	// On the customer's phone the target is "from me" and the remote
	// address is ours. In the CRM this is an incoming row in the customer's DM.
	event.Info.Chat = participant
	message.ProtocolMessage.Key.RemoteJID = proto.String("15550000009@s.whatsapp.net")
	_, direct, _, _ := MessageEnvelope(event)
	if direct.Revoke == nil || direct.Revoke.FromMe || direct.Revoke.Chat != participant.String() {
		t.Fatal("customer revoke was not bound to its incoming DM")
	}
	message.ProtocolMessage.Key.FromMe = proto.Bool(false)
	if _, other, _, _ := MessageEnvelope(event); other.Revoke != nil {
		t.Fatal("a remote participant cannot revoke another author's row")
	}
	j := journal(t, database(t, filepath.Join(t.TempDir(), "local-delete.db")), testScope)
	if !j.Capture(context.Background(), &events.DeleteForMe{ChatJID: chat, MessageID: "LOCAL_ONLY"}, nil) {
		t.Fatal("local DeleteForMe event should be safely ignored")
	}
	rows, err := j.Events(context.Background(), 0, 10)
	if err != nil || len(rows) != 0 {
		t.Fatal("local-only delete must not be projected as Delete for everyone", err)
	}
}
