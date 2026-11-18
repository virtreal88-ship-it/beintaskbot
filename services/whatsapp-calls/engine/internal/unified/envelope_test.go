package unified

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

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
