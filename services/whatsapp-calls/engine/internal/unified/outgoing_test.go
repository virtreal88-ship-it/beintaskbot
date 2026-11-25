package unified

import (
	"context"
	"encoding/json"
	"errors"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
	"path/filepath"
	"testing"
)

type outgoingClient struct {
	uploads, sends int
	fail           bool
	message        *waE2E.Message
}

func (c *outgoingClient) GenerateMessageID() types.MessageID { return "SYNTHETIC_ID" }
func (c *outgoingClient) Upload(_ context.Context, b []byte, _ whatsmeow.MediaType) (whatsmeow.UploadResponse, error) {
	c.uploads++
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
func TestOutgoingAudioMIMEOnlyDeclaresOpusForVoiceNotes(t *testing.T) {
	for _, tc := range []struct {
		kind, wireMIME string
		ptt           bool
	}{
		{"voice", "audio/ogg; codecs=opus", true},
		{"audio", "audio/ogg", false},
	} {
		t.Run(tc.kind, func(t *testing.T) {
			ctx := context.Background()
			j := journal(t, database(t, filepath.Join(t.TempDir(), "audio.db")), testScope)
			client := &outgoingClient{}
			req := Outgoing{Scope: testScope, Operation: "audio-op", Chat: "15550000002@s.whatsapp.net", Kind: tc.kind, MIME: "audio/ogg", Data: []byte("synthetic")}
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
		})
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
