package unified

import (
	"context"
	"encoding/json"
	"go.mau.fi/whatsmeow/types/events"
)

// Capture acknowledges protocol messages only after the encrypted journal commit.
func (j *Journal) Capture(ctx context.Context, raw any) bool {
	switch event := raw.(type) {
	case *events.Message:
		key, envelope, retain, err := MessageEnvelope(event)
		if err != nil {
			return false
		}
		if !retain {
			return true
		}
		payload, err := json.Marshal(envelope)
		if err != nil {
			return false
		}
		_, err = j.Append(ctx, key, payload)
		return err == nil
	case *events.Receipt:
		for _, id := range event.MessageIDs {
			envelope := Envelope{Kind: "receipt", Chat: event.Chat.String(), Sender: event.Sender.String(), ProviderID: id,
				FromMe: event.IsFromMe, Timestamp: event.Timestamp.UnixMilli(), Status: string(event.Type)}
			key, _ := json.Marshal([]any{"receipt", envelope.Chat, envelope.Sender, id, envelope.Status, envelope.Timestamp})
			payload, err := json.Marshal(envelope)
			if err != nil {
				return false
			}
			if _, err = j.Append(ctx, string(key), payload); err != nil {
				return false
			}
		}
	}
	return true
}
