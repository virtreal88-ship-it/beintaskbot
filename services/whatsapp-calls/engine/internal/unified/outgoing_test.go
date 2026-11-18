package unified

import (
	"context"
	"errors"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
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
