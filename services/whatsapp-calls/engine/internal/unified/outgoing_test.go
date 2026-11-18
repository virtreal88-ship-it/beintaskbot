package unified

import (
	"context"
	"encoding/json"
	"errors"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
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
		if _, err := j.Deliver(context.Background(), client, request); err != nil {
			t.Fatal(err)
		}
	}
	if client.uploads != 1 || client.sends != 1 || client.message.GetDocumentMessage().GetFileName() != "test.txt" {
		t.Fatal("duplicate or wrong media")
	}
	request.Data = []byte("changed")
	if _, err := j.Deliver(context.Background(), client, request); !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	if client.uploads != 1 || client.sends != 1 {
		t.Fatal("conflict reached provider")
	}
}
func TestOutgoingUnknownCannotResend(t *testing.T) {
	j := journal(t, database(t, filepath.Join(t.TempDir(), "send.db")), testScope)
	client := &outgoingClient{fail: true}
	request := Outgoing{Scope: testScope, Operation: "op1", Chat: "15550000002@s.whatsapp.net", Kind: "text", Text: "synthetic"}
	for i := 0; i < 2; i++ {
		if _, err := j.Deliver(context.Background(), client, request); !errors.Is(err, ErrUnknown) {
			t.Fatal(err)
		}
	}
	if client.sends != 1 {
		t.Fatal("unknown operation sent again")
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
		if _, err := j.Deliver(context.Background(), client, request); !errors.Is(err, ErrConfiguration) {
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
	if _, err = j.Deliver(ctx, c, bad); !errors.Is(err, ErrConflict) {
		t.Fatal("cross chat quote", err)
	}
	bad = req
	bad.QuoteID = "OTHER_123"
	if _, err = j.Deliver(ctx, c, bad); !errors.Is(err, ErrConflict) {
		t.Fatal("wrong quote", err)
	}
	if c.sends != 0 {
		t.Fatal("invalid quote sent")
	}
	for i := 0; i < 2; i++ {
		if _, err = j.Deliver(ctx, c, req); err != nil {
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
	if _, err := j.Deliver(ctx, c, request); err != nil {
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
	if _, err = j.Deliver(ctx, c, req); err != nil {
		t.Fatal(err)
	}
	if c.message.GetExtendedTextMessage().GetContextInfo().GetRemoteJID() != pn {
		t.Fatal("wrong quote chat")
	}
	e.SenderAlt = "15550000003@s.whatsapp.net"
	body, _ = json.Marshal(e)
	req.QuoteEvent, _ = j.Append(ctx, "wrong-alias", body)
	req.Operation = "other"
	if _, err = j.Deliver(ctx, c, req); !errors.Is(err, ErrConflict) {
		t.Fatal("guessed alias accepted", err)
	}
	if c.sends != 1 {
		t.Fatal("bad alias sent")
	}
}
