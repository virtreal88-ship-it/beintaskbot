package unified

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
)

type outgoingClient struct {
	mediaType      whatsmeow.MediaType
	mediaBytes     []byte
	uploads, sends int
	fail           bool
	message        *waE2E.Message
	id             string
}

func (c *outgoingClient) GenerateMessageID() types.MessageID {
	if c.id != "" {
		return types.MessageID(c.id)
	}
	return "SYNTHETIC_ID"
}
func (c *outgoingClient) BuildEdit(chat types.JID, id types.MessageID, content *waE2E.Message) *waE2E.Message {
	return &waE2E.Message{EditedMessage: &waE2E.FutureProofMessage{Message: &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{
		Key:  &waCommon.MessageKey{FromMe: proto.Bool(true), ID: proto.String(string(id)), RemoteJID: proto.String(chat.String())},
		Type: waE2E.ProtocolMessage_MESSAGE_EDIT.Enum(), EditedMessage: content, TimestampMS: proto.Int64(time.Now().UnixMilli()),
	}}}}
}
func (c *outgoingClient) BuildRevoke(chat, sender types.JID, id types.MessageID) *waE2E.Message {
	return &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{
		Key:  &waCommon.MessageKey{FromMe: proto.Bool(sender.IsEmpty()), ID: proto.String(string(id)), RemoteJID: proto.String(chat.String())},
		Type: waE2E.ProtocolMessage_REVOKE.Enum(),
	}}
}
func (c *outgoingClient) Upload(_ context.Context, b []byte, mediaType whatsmeow.MediaType) (whatsmeow.UploadResponse, error) {
	c.uploads++
	c.mediaType, c.mediaBytes = mediaType, append([]byte(nil), b...)
	return whatsmeow.UploadResponse{FileLength: uint64(len(b))}, nil
}
func (c *outgoingClient) SendMessage(_ context.Context, _ types.JID, m *waE2E.Message, x ...whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error) {
	c.sends++
	c.message = m
	if c.fail {
		return whatsmeow.SendResponse{}, ErrUnknown
	}
	return whatsmeow.SendResponse{ID: x[0].ID}, nil
}
func TestOutgoingReplayAndConflict(t *testing.T) {
	j := journal(t, database(t, filepath.Join(t.TempDir(), "send.db")), testScope)
	client := &outgoingClient{}
	request := Outgoing{Scope: testScope, Operation: "op1", Chat: "15550000002@s.whatsapp.net", Kind: "document", MIME: "text/plain", Name: "test.txt", Data: []byte("synthetic")}
	for i := 0; i < 2; i++ {
		if _, err := j.Deliver(context.Background(), client, request, nil); err != nil {
			t.Fatal(err)
		}
	}
	if client.uploads != 1 || client.sends != 1 || client.message.GetDocumentMessage().GetFileName() != "test.txt" {
		t.Fatal("duplicate or wrong media")
	}
	request.Data = []byte("changed")
	if _, err := j.Deliver(context.Background(), client, request, nil); !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	if client.uploads != 1 || client.sends != 1 {
		t.Fatal("conflict reached provider")
	}
}

func TestGroupMembershipIsCheckedImmediatelyBeforeProviderSend(t *testing.T) {
	j := journal(t, database(t, filepath.Join(t.TempDir(), "group.db")), testScope)
	ctx := context.Background()
	client := &outgoingClient{}
	request := Outgoing{Scope: testScope, Operation: "group1", Chat: "120363123456789@g.us",
		Kind: "document", MIME: "text/plain", Name: "test.txt", Data: []byte("synthetic"), VerifyGroupMembership: true}
	if _, err := j.Deliver(ctx, client, request, nil); !errors.Is(err, ErrConfiguration) {
		t.Fatal("a verified group operation was possible without the provider check", err)
	}
	if _, err := j.Deliver(ctx, client, request, nil, func(context.Context) error {
		if client.uploads != 1 || client.sends != 0 {
			t.Fatal("group proof was not checked after preparation and immediately before send")
		}
		return ErrGroupMembership
	}); !errors.Is(err, ErrGroupMembership) || client.sends != 0 {
		t.Fatal("an unconfirmed group was sent a message", err)
	}
	if _, err := j.Deliver(ctx, client, request, nil, func(context.Context) error { return nil }); err != nil {
		t.Fatal("a definitively unsent command was blocked", err)
	}
	if _, err := j.Deliver(ctx, client, request, nil, func(context.Context) error {
		t.Fatal("an accepted command was rechecked instead of returning its stored result")
		return nil
	}); err != nil || client.sends != 1 {
		t.Fatal("the group command was delivered twice", err)
	}
}

func TestOwnTextEditAndRevokeUseWhatsmeowControlMessages(t *testing.T) {
	ctx := context.Background()
	for _, action := range []string{"edit", "delete"} {
		t.Run(action, func(t *testing.T) {
			j := journal(t, database(t, filepath.Join(t.TempDir(), "message-action.db")), testScope)
			client := &outgoingClient{}
			chat := "15550000002@s.whatsapp.net"
			if _, err := j.Deliver(ctx, client, Outgoing{Scope: testScope, Operation: "employee:11:original",
				Chat: chat, Kind: "text", Text: "original"}, nil); err != nil {
				t.Fatal(err)
			}
			client.id = "SYNTHETIC_ACTION_ID"
			requestID := "4d29b630-4df7-4faf-b35d-0af3f4a9d4c3"
			request := Outgoing{Scope: testScope, Operation: action + ":11:" + requestID, Chat: chat, Kind: action}
			if action == "edit" {
				request.Text = "corrected text"
				request.Edit = &EditTarget{ID: "SYNTHETIC_ID", OriginalMS: time.Now().Add(-time.Minute).UnixMilli()}
			} else {
				request.Delete = &DeleteTarget{ID: "SYNTHETIC_ID", OriginalMS: time.Now().Add(-time.Minute).UnixMilli()}
			}
			if _, err := j.Deliver(ctx, client, request, nil); err != nil {
				t.Fatal(err)
			}
			if client.sends != 2 {
				t.Fatalf("expected one original and one action; got %d", client.sends)
			}
			if action == "edit" {
				protocol := client.message.GetEditedMessage().GetMessage().GetProtocolMessage()
				if protocol.GetType() != waE2E.ProtocolMessage_MESSAGE_EDIT || protocol.GetKey().GetID() != "SYNTHETIC_ID" ||
					protocol.GetKey().GetRemoteJID() != chat || protocol.GetEditedMessage().GetConversation() != "corrected text" {
					t.Fatal("edit did not target the original WhatsApp text")
				}
			} else if client.message.GetProtocolMessage().GetType() != waE2E.ProtocolMessage_REVOKE ||
				client.message.GetProtocolMessage().GetKey().GetID() != "SYNTHETIC_ID" ||
				!client.message.GetProtocolMessage().GetKey().GetFromMe() {
				t.Fatal("revoke did not target the sender's own original message")
			}
			events, err := j.Events(ctx, 0, 10)
			if err != nil || len(events) != 1 {
				t.Fatalf("expected a confirmed control event: %v %v", events, err)
			}
			var envelope Envelope
			if err := json.Unmarshal(events[0].Payload, &envelope); err != nil || envelope.Variant != "control" ||
				(action == "edit" && (envelope.Edit == nil || envelope.Edit.Operation != request.Operation)) ||
				(action == "delete" && (envelope.Revoke == nil || envelope.Revoke.Operation != request.Operation)) {
				t.Fatal("confirmed action was not retained as a scoped control event", err)
			}
		})
	}
}

func TestMessageActionExpiryAndUnknownOutcomeNeverRepeat(t *testing.T) {
	ctx := context.Background()
	j := journal(t, database(t, filepath.Join(t.TempDir(), "message-action-unknown.db")), testScope)
	client := &outgoingClient{}
	chat := "15550000002@s.whatsapp.net"
	if _, err := j.Deliver(ctx, client, Outgoing{Scope: testScope, Operation: "employee:11:original", Chat: chat, Kind: "text", Text: "original"}, nil); err != nil {
		t.Fatal(err)
	}
	client.id = "SYNTHETIC_EDIT_ID"
	request := Outgoing{Scope: testScope, Operation: "edit:11:4d29b630-4df7-4faf-b35d-0af3f4a9d4c3", Chat: chat, Kind: "edit", Text: "new text",
		Edit: &EditTarget{ID: "SYNTHETIC_ID", OriginalMS: time.Now().Add(-16 * time.Minute).UnixMilli()}}
	if _, err := j.Deliver(ctx, client, request, nil); err != ErrEditExpired || client.sends != 1 {
		t.Fatalf("expired edit reached provider: err=%v sends=%d", err, client.sends)
	}
	request.Operation = "edit:11:5d29b630-4df7-4faf-b35d-0af3f4a9d4c3"
	request.Edit.OriginalMS = time.Now().Add(-time.Minute).UnixMilli()
	client.fail = true
	if _, err := j.Deliver(ctx, client, request, nil); err != ErrUnknown {
		t.Fatal(err)
	}
	client.fail = false
	if _, err := j.Deliver(ctx, client, request, nil); err != ErrUnknown || client.sends != 2 {
		t.Fatalf("unknown edit was retried: err=%v sends=%d", err, client.sends)
	}
}
func TestOutgoingAudioMIMEAndDurationOnlyForVoiceNotes(t *testing.T) {
	voice, err := os.ReadFile("testdata/browser_voice_1200ms.ogg")
	if err != nil {
		t.Fatal(err)
	}

	for _, tc := range []struct {
		kind, wireMIME string
		ptt            bool
	}{
		{"voice", "audio/ogg; codecs=opus", true},
		{"audio", "audio/ogg", false},
	} {
		t.Run(tc.kind, func(t *testing.T) {
			ctx := context.Background()
			j := journal(t, database(t, filepath.Join(t.TempDir(), "audio.db")), testScope)
			client := &outgoingClient{}
			req := Outgoing{Scope: testScope, Operation: "audio-op", Chat: "15550000002@s.whatsapp.net", Kind: tc.kind, MIME: "audio/ogg", Data: voice}
			for i := 0; i < 2; i++ {
				if _, err := j.Deliver(ctx, client, req, nil); err != nil {
					t.Fatal(err)
				}
			}
			audio := client.message.GetAudioMessage()
			if client.uploads != 1 || client.sends != 1 || audio == nil ||
				audio.GetPTT() != tc.ptt || audio.GetMimetype() != tc.wireMIME {
				t.Fatal("unexpected wire audio format or duplicate send")
			}
			if client.mediaType != whatsmeow.MediaAudio || !bytes.Equal(client.mediaBytes, voice) ||
				audio.GetFileLength() != uint64(len(voice)) {
				t.Fatal("unexpected audio encryption/upload category or changed bytes")
			}
			if (tc.ptt && audio.GetSeconds() != 2) || (!tc.ptt && audio.Seconds != nil) {
				t.Fatalf("unexpected wire duration: %v", audio.Seconds)
			}
			events, err := j.Events(ctx, 0, 10)
			if err != nil || len(events) != 1 {
				t.Fatalf("expected one scoped history entry: %v", err)
			}
			var envelope Envelope
			var retained waE2E.Message
			if err := json.Unmarshal(events[0].Payload, &envelope); err != nil {
				t.Fatal(err)
			}
			if err := proto.Unmarshal(envelope.Proto, &retained); err != nil {
				t.Fatal(err)
			}
			if !envelope.AppDispatch || !envelope.FromMe || envelope.Chat != req.Chat ||
				envelope.MediaKind != "audio" || envelope.MediaSize != uint64(len(voice)) ||
				!proto.Equal(audio, retained.GetAudioMessage()) {
				t.Fatal("authorized retained event does not match sent audio")
			}
		})
	}
}

func TestOutgoingVoiceWithoutProvenDurationDoesNotReachUpload(t *testing.T) {
	voice, err := os.ReadFile("testdata/browser_voice_1200ms.ogg")
	if err != nil {
		t.Fatal(err)
	}
	j := journal(t, database(t, filepath.Join(t.TempDir(), "invalid-audio.db")), testScope)
	client := &outgoingClient{}
	last := bytes.LastIndex(voice, []byte("OggS"))
	if last < 0 {
		t.Fatal("invalid fixture")
	}
	overlong, wrongSequence := bytes.Clone(voice), bytes.Clone(voice)
	binary.LittleEndian.PutUint64(overlong[last+6:], 48000*121)
	binary.LittleEndian.PutUint32(wrongSequence[last+18:], 999)
	for _, data := range [][]byte{[]byte("non-ogg"), voice[:len(voice)-1], append(bytes.Clone(voice), voice...), overlong, wrongSequence} {
		req := Outgoing{Scope: testScope, Operation: "invalid-audio", Chat: "15550000002@s.whatsapp.net", Kind: "voice", MIME: "audio/ogg", Data: data}
		if _, err := j.Deliver(context.Background(), client, req, nil); !errors.Is(err, ErrMediaRestricted) {
			t.Fatalf("invalid recording was not rejected before dispatch: %v", err)
		}
	}
	if client.uploads != 0 || client.sends != 0 {
		t.Fatal("bad voice reached provider")
	}
}

func TestOutgoingUnknownCannotResend(t *testing.T) {
	j := journal(t, database(t, filepath.Join(t.TempDir(), "send.db")), testScope)
	client := &outgoingClient{fail: true}
	request := Outgoing{Scope: testScope, Operation: "op1", Chat: "15550000002@s.whatsapp.net", Kind: "text", Text: "synthetic"}
	for i := 0; i < 2; i++ {
		if _, err := j.Deliver(context.Background(), client, request, nil); !errors.Is(err, ErrUnknown) {
			t.Fatal(err)
		}
	}
	if client.sends != 1 {
		t.Fatal("unknown operation sent again")
	}
	items, err := j.Events(context.Background(), 0, 50)
	if err != nil || len(items) != 0 {
		t.Fatal("unconfirmed send became a confirmed history event", err)
	}
}

func TestOutgoingMediaEvidenceSurvivesRestartWithoutResending(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "media.db")
	db := database(t, path)
	j := journal(t, db, testScope)
	c := &outgoingClient{}
	req := Outgoing{Scope: testScope, Operation: "media", Chat: "15550000002@s.whatsapp.net", Kind: "image", MIME: "image/png", Data: []byte("synthetic image"), Text: "caption"}
	if _, err := j.Deliver(ctx, c, req, nil); err != nil {
		t.Fatal(err)
	}
	items, err := j.Events(ctx, 0, 50)
	if err != nil || len(items) != 1 {
		t.Fatal("missing media evidence", err)
	}
	var e Envelope
	if json.Unmarshal(items[0].Payload, &e) != nil || e.Chat != req.Chat || e.Sender != testScope.Account || !e.FromMe || e.MediaMime != req.MIME || e.MediaSize != uint64(len(req.Data)) || e.Text != req.Text {
		t.Fatal("wrong scoped media evidence")
	}
	var m waE2E.Message
	if proto.Unmarshal(e.Proto, &m) != nil || m.GetImageMessage().GetCaption() != req.Text {
		t.Fatal("retained protobuf lost")
	}
	db.Close()
	j = journal(t, database(t, path), testScope)
	if _, err = j.Deliver(ctx, c, req, nil); err != nil {
		t.Fatal(err)
	}
	again, err := j.Events(ctx, 0, 50)
	if err != nil || len(again) != 1 || again[0].ID != items[0].ID || c.sends != 1 || c.uploads != 1 {
		t.Fatal("restart duplicated media", err)
	}
}

func TestMediaEvidenceFailureNeverResendsAcceptedProviderCall(t *testing.T) {
	ctx := context.Background()
	db := database(t, filepath.Join(t.TempDir(), "failure.db"))
	j := journal(t, db, testScope)
	if _, err := db.Exec(`CREATE TRIGGER fail_media BEFORE INSERT ON unified_events BEGIN SELECT RAISE(FAIL,'synthetic'); END`); err != nil {
		t.Fatal(err)
	}
	c := &outgoingClient{}
	req := Outgoing{Scope: testScope, Operation: "media", Chat: "15550000002@s.whatsapp.net", Kind: "image", MIME: "image/png", Data: []byte("synthetic")}
	for i := 0; i < 2; i++ {
		if _, err := j.Deliver(ctx, c, req, nil); !errors.Is(err, ErrUnknown) {
			t.Fatal(err)
		}
	}
	if c.sends != 1 || c.uploads != 1 {
		t.Fatal("evidence failure repeated provider send")
	}
}
func TestOutgoingScopeCannotBeOverridden(t *testing.T) {
	j := journal(t, database(t, filepath.Join(t.TempDir(), "send.db")), testScope)
	for _, field := range []string{"tenant", "source", "account", "generation"} {
		request := Outgoing{Scope: testScope, Operation: "op1", Chat: "15550000002@s.whatsapp.net", Kind: "text", Text: "synthetic"}
		switch field {
		case "tenant":
			request.Scope.Tenant = "other"
		case "source":
			request.Scope.Source = "other"
		case "account":
			request.Scope.Account = "other"
		case "generation":
			request.Scope.Generation = "other"
		}
		client := &outgoingClient{}
		if _, err := j.Deliver(context.Background(), client, request, nil); !errors.Is(err, ErrConfiguration) {
			t.Fatal(field, err)
		}
		if client.sends != 0 || client.uploads != 0 {
			t.Fatal("scope escaped")
		}
	}
}

func TestQuoteUsesRetainedExactMessage(t *testing.T) {
	ctx := context.Background()
	j := journal(t, database(t, filepath.Join(t.TempDir(), "quote.db")), testScope)
	chat := "15550000002@s.whatsapp.net"
	original, _ := proto.Marshal(&waE2E.Message{Conversation: proto.String("synthetic original")})
	body, _ := json.Marshal(Envelope{Kind: "message", Chat: chat, Sender: chat, ProviderID: "ORIGINAL_123", Proto: original})
	event, err := j.Append(ctx, "original", body)
	if err != nil {
		t.Fatal(err)
	}
	req := Outgoing{Scope: testScope, Operation: "quote", Chat: chat, Kind: "text", Text: "synthetic reply", QuoteEvent: event, QuoteID: "ORIGINAL_123"}
	c := &outgoingClient{}
	bad := req
	bad.Chat = "15550000003@s.whatsapp.net"
	if _, err = j.Deliver(ctx, c, bad, nil); !errors.Is(err, ErrQuoteUnavailable) {
		t.Fatal("cross chat quote", err)
	}
	bad = req
	bad.QuoteID = "OTHER_123"
	if _, err = j.Deliver(ctx, c, bad, nil); !errors.Is(err, ErrQuoteUnavailable) {
		t.Fatal("wrong quote", err)
	}
	if c.sends != 0 {
		t.Fatal("invalid quote sent")
	}
	for i := 0; i < 2; i++ {
		if _, err = j.Deliver(ctx, c, req, nil); err != nil {
			t.Fatal(err)
		}
	}
	q := c.message.GetExtendedTextMessage().GetContextInfo()
	if c.sends != 1 || q.GetStanzaID() != "ORIGINAL_123" || q.GetParticipant() != chat || q.GetQuotedMessage().GetConversation() != "synthetic original" {
		t.Fatal("quote lost or sent twice")
	}
}

func TestLookupRequiresExactActorOperationAndPeer(t *testing.T) {
	ctx := context.Background()
	j := journal(t, database(t, filepath.Join(t.TempDir(), "lookup.db")), testScope)
	c := &outgoingClient{}
	request := Outgoing{Scope: testScope, Operation: "employee:11:op1", Chat: "15550000002@s.whatsapp.net", Kind: "text", Text: "synthetic"}
	if _, err := j.Deliver(ctx, c, request, nil); err != nil {
		t.Fatal(err)
	}
	for _, pair := range [][2]string{{"employee:12:op1", request.Chat}, {request.Operation, "15550000003@s.whatsapp.net"}} {
		if _, err := j.Lookup(ctx, pair[0], pair[1]); err == nil {
			t.Fatal("lookup escaped scope")
		}
	}
	result, err := j.Lookup(ctx, request.Operation, request.Chat)
	if err != nil || result.State != "accepted" || c.sends != 1 {
		t.Fatal("lookup resends or loses acceptance", err)
	}
}

func TestQuoteOnlyUsesProviderConfirmedPNLIDPair(t *testing.T) {
	ctx := context.Background()
	j := journal(t, database(t, filepath.Join(t.TempDir(), "alias.db")), testScope)
	pn := "15550000002@s.whatsapp.net"
	lid := "1234567890@lid"
	original, _ := proto.Marshal(&waE2E.Message{Conversation: proto.String("synthetic original")})
	e := Envelope{Kind: "message", Chat: lid, Sender: lid, SenderAlt: pn, ProviderID: "ORIGINAL_ALIAS", Proto: original}
	body, _ := json.Marshal(e)
	event, err := j.Append(ctx, "alias", body)
	if err != nil {
		t.Fatal(err)
	}
	req := Outgoing{Scope: testScope, Operation: "alias", Chat: pn, Kind: "text", Text: "synthetic reply", QuoteEvent: event, QuoteID: e.ProviderID}
	c := &outgoingClient{}
	if _, err = j.Deliver(ctx, c, req, nil); err != nil {
		t.Fatal(err)
	}
	if c.message.GetExtendedTextMessage().GetContextInfo().GetRemoteJID() != pn {
		t.Fatal("wrong quote chat")
	}
	e.SenderAlt = "15550000003@s.whatsapp.net"
	body, _ = json.Marshal(e)
	req.QuoteEvent, _ = j.Append(ctx, "wrong-alias", body)
	req.Operation = "other"
	if _, err = j.Deliver(ctx, c, req, nil); !errors.Is(err, ErrQuoteUnavailable) {
		t.Fatal("guessed alias accepted", err)
	}
	if c.sends != 1 {
		t.Fatal("bad alias sent")
	}
}

type quoteMapping struct {
	store.LIDStore
	lid, phone types.JID
	badReverse bool
}

func (m quoteMapping) GetPNForLID(_ context.Context, lid types.JID) (types.JID, error) {
	if lid == m.lid {
		return m.phone, nil
	}
	return types.EmptyJID, nil
}

func (m quoteMapping) GetLIDForPN(_ context.Context, phone types.JID) (types.JID, error) {
	if phone == m.phone && !m.badReverse {
		return m.lid, nil
	}
	return types.EmptyJID, nil
}

func TestHistoricalQuoteUsesPairedDeviceMapping(t *testing.T) {
	ctx := context.Background()
	j := journal(t, database(t, filepath.Join(t.TempDir(), "historical-alias.db")), testScope)
	pn := types.NewJID("15550000002", types.DefaultUserServer)
	lid := types.NewJID("1234567890", types.HiddenUserServer)
	original, _ := proto.Marshal(&waE2E.Message{Conversation: proto.String("synthetic original")})
	e := Envelope{Kind: "message", Chat: lid.String(), Sender: lid.String(), ProviderID: "ORIGINAL_HISTORICAL", Proto: original}
	body, _ := json.Marshal(e)
	event, err := j.Append(ctx, "historical", body)
	if err != nil {
		t.Fatal(err)
	}
	req := Outgoing{Scope: testScope, Operation: "employee:11:historical", Chat: pn.String(), Kind: "text", Text: "reply", QuoteEvent: event, QuoteID: e.ProviderID}
	c := &outgoingClient{}
	mapping := quoteMapping{lid: lid, phone: pn}
	for _, lids := range []store.LIDStore{nil, quoteMapping{lid: lid, phone: pn, badReverse: true}} {
		if _, err := j.Deliver(ctx, c, req, lids); !errors.Is(err, ErrQuoteUnavailable) {
			t.Fatal("missing identity evidence must disallow dispatch", err)
		}
	}
	if _, err := j.Lookup(ctx, req.Operation, req.Chat); err == nil || c.sends != 0 {
		t.Fatal("a refused quote reserved or sent the operation")
	}
	for i := 0; i < 2; i++ {
		if _, err := j.Deliver(ctx, c, req, mapping); err != nil {
			t.Fatal(err)
		}
	}
	quote := c.message.GetExtendedTextMessage().GetContextInfo()
	if c.sends != 1 || quote.GetStanzaID() != e.ProviderID || quote.GetParticipant() != lid.String() || quote.GetRemoteJID() != pn.String() {
		t.Fatal("historical quote context lost or sent twice")
	}
	if _, err := j.Deliver(ctx, c, req, nil); !errors.Is(err, ErrConflict) {
		t.Fatal("an existing operation cannot later be classified as never sent", err)
	}
}

func TestLegacyQuoteScopeAndIdempotency(t *testing.T) {
	ctx := context.Background()
	j := journal(t, database(t, filepath.Join(t.TempDir(), "legacy-quote.db")), testScope)
	peer := "15550000002@s.whatsapp.net"
	req := Outgoing{Scope: testScope, Operation: "legacy-quote", Chat: peer, Kind: "text", Text: "reply", QuoteID: "LEGACY_123", LegacyQuote: &LegacyQuote{Chat: peer, ProviderID: "LEGACY_123", Sender: peer, Text: "old original"}}
	c := &outgoingClient{}
	bad := req
	bad.Chat = "15550000003@s.whatsapp.net"
	if _, err := j.Deliver(ctx, c, bad, nil); !errors.Is(err, ErrConfiguration) {
		t.Fatal("cross chat historical quote", err)
	}
	for i := 0; i < 2; i++ {
		if _, err := j.Deliver(ctx, c, req, nil); err != nil {
			t.Fatal(err)
		}
	}
	if c.sends != 1 || c.message.GetExtendedTextMessage().GetContextInfo().GetQuotedMessage().GetConversation() != "old original" {
		t.Fatal("historical quote lost or resent")
	}
	req.LegacyQuote.Text = "changed"
	if _, err := j.Deliver(ctx, c, req, nil); !errors.Is(err, ErrConflict) {
		t.Fatal("changed historical quote replayed")
	}
}
