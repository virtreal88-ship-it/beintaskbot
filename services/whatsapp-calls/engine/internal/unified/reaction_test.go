package unified

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types/events"

	"go.mau.fi/whatsmeow/types"
)

type reactionClient struct {
	outgoingClient
	next int
}

func (c *reactionClient) GenerateMessageID() types.MessageID {
	c.next++
	return fmt.Sprintf("SYNTHETIC_%d", c.next)
}

func TestDeliverReactionKeepsExactTargetAndUsesDurableReceipt(t *testing.T) {
	for _, tc := range []struct {
		name, chat, participant string
		fromMe                  bool
	}{
		{"direct incoming", "15550000002@s.whatsapp.net", "", false},
		{"direct outgoing LID", "15550000002@lid", "", true},
		{"group incoming", "15550000003@g.us", "15550000002@lid", false},
		{"group outgoing", "15550000003@g.us", "", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx := context.Background()
			j := journal(t, database(t, filepath.Join(t.TempDir(), "reaction.db")), testScope)
			client := &reactionClient{}
			target := &ReactionTarget{ID: "ORIGINAL_MESSAGE", FromMe: tc.fromMe, Participant: tc.participant}
			for i, emoji := range []string{"👍", "❤️", ""} {
				op := []string{"add", "change", "remove"}[i]
				request := Outgoing{Scope: testScope, Operation: op, Chat: tc.chat, Kind: "reaction", Text: emoji, Reaction: target}
				for range 2 {
					result, err := j.Deliver(ctx, client, request, nil)
					if err != nil || result.State != "accepted" {
						t.Fatalf("expected accepted receipt: %+v %v", result, err)
					}
				}
				reaction := client.message.GetReactionMessage()
				key := reaction.GetKey()
				if key.GetID() != target.ID || key.GetRemoteJID() != tc.chat ||
					key.GetFromMe() != tc.fromMe || key.GetParticipant() != tc.participant || reaction.GetText() != emoji {
					t.Fatal("wrong reaction key or emoji")
				}
			}
			if client.sends != 3 || client.uploads != 0 {
				t.Fatal("unexpected duplicate dispatch or upload")
			}
			items, err := j.Events(ctx, 0, 10)
			if err != nil || len(items) != 3 {
				t.Fatalf("missing durable reaction evidence: %d, %v", len(items), err)
			}
			var removal Envelope
			if json.Unmarshal(items[2].Payload, &removal) != nil || removal.Variant != "control" ||
				removal.Reaction == nil || *removal.Reaction != *target || removal.Text != "" || !removal.AppDispatch {
				t.Fatal("removal was not captured as control evidence")
			}
		})
	}
}

func TestReactionWithoutParticipantAndUnknownReplay(t *testing.T) {
	ctx := context.Background()
	j := journal(t, database(t, filepath.Join(t.TempDir(), "reaction.db")), testScope)
	client := &outgoingClient{fail: true}
	request := Outgoing{Scope: testScope, Operation: "op", Chat: "15550000003@g.us", Kind: "reaction",
		Text: "👍", Reaction: &ReactionTarget{ID: "ORIGINAL_MESSAGE"}}
	if _, err := j.Deliver(ctx, client, request, nil); !errors.Is(err, ErrConfiguration) || client.sends != 0 {
		t.Fatal("ambiguous target reached provider")
	}
	request.Reaction.Participant = "15550000002@lid"
	for range 2 {
		if _, err := j.Deliver(ctx, client, request, nil); !errors.Is(err, ErrUnknown) {
			t.Fatal(err)
		}
	}
	if client.sends != 1 {
		t.Fatal("unconfirmed reaction was retried")
	}
	if result, err := j.Lookup(ctx, request.Operation, request.Chat); err != nil || result.State != "unknown" {
		t.Fatal("missing read-only status", err)
	}
	if _, err := j.Lookup(ctx, request.Operation, "15550000004@g.us"); err == nil {
		t.Fatal("wrong chat could read the receipt")
	}
}

func TestLinkedPhoneReactionIsControlEvidence(t *testing.T) {
	chat, _ := types.ParseJID("15550000003@g.us")
	client := &outgoingClient{}
	j := journal(t, database(t, filepath.Join(t.TempDir(), "reaction.db")), testScope)
	request := Outgoing{Scope: testScope, Operation: "op", Chat: chat.String(), Kind: "reaction",
		Text: "😂", Reaction: &ReactionTarget{ID: "ORIGINAL_MESSAGE", Participant: "15550000002@lid"}}
	if _, err := j.Deliver(context.Background(), client, request, nil); err != nil {
		t.Fatal(err)
	}
	phone := reactionFixture(chat, client.message, true)
	_, evidence, _, err := MessageEnvelope(phone)
	if err != nil || evidence.AppDispatch || evidence.Reaction == nil || evidence.Text != "😂" || evidence.Variant != "control" {
		t.Fatal("linked-phone change not recognized", err)
	}
	phone.Info.IsFromMe = false
	_, evidence, _, err = MessageEnvelope(phone)
	if err != nil || evidence.Reaction != nil || evidence.Variant != "control" {
		t.Fatal("external participant change applied to linked account", err)
	}
}

func reactionFixture(chat types.JID, message *waE2E.Message, fromMe bool) *events.Message {
	return &events.Message{
		Info:    types.MessageInfo{MessageSource: types.MessageSource{Chat: chat, IsFromMe: fromMe}, ID: "PHONE_REACTION", Timestamp: time.Now()},
		Message: message,
	}
}
