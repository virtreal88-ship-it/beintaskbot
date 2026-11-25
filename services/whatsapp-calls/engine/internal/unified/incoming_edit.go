package unified

import (
	"context"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

// FromMe in the inner key is relative to the customer. The containing stanza
// identifies the CRM chat, even when the inner DM key names our linked account.
func incomingEditTarget(event *events.Message, key *waCommon.MessageKey, device *store.Device) bool {
	if !key.GetFromMe() || !validProviderID(key.GetID()) || event.Info.IsFromMe {
		return false
	}
	sender, alternate := event.Info.Sender.ToNonAD(), event.Info.SenderAlt.ToNonAD()
	if !reactionUser(sender) {
		return false
	}
	chat := event.Info.Chat
	if chat.Server == types.GroupServer {
		if key.GetRemoteJID() != chat.String() {
			return false
		}
		if key.GetParticipant() == "" {
			return true
		}
		participant, err := types.ParseJID(key.GetParticipant())
		return err == nil && (participant.ToNonAD() == sender || reactionChatAlias(sender, alternate, participant.ToNonAD().String()))
	}
	if !reactionUser(chat) || (chat != sender && !reactionChatAlias(sender, alternate, chat.String())) {
		return false
	}
	target, err := types.ParseJID(key.GetRemoteJID())
	return err == nil && reactionUser(target) && (target == chat || target == sender || reactionChatAlias(sender, alternate, target.String()) ||
		target == device.GetJID().ToNonAD() || target == device.GetLID().ToNonAD())
}

func attachIncomingTextEdit(ctx context.Context, out *Envelope, event *events.Message, client *whatsmeow.Client) {
	message := event.Message
	secret := message.GetSecretEncryptedMessage()
	if secret != nil {
		if secret.GetSecretEncType() != waE2E.SecretEncryptedMessage_MESSAGE_EDIT || len(secret.GetEncIV()) != 12 ||
			!incomingEditTarget(event, secret.GetTargetMessageKey(), client.Store) {
			return
		}
		plain, err := client.DecryptSecretEncryptedMessage(ctx, event)
		if err != nil {
			return
		}
		message = plain
	}
	change := editProtocol(message)
	if change.GetType() != waE2E.ProtocolMessage_MESSAGE_EDIT || change.GetTimestampMS() <= 0 ||
		!incomingEditTarget(event, change.GetKey(), client.Store) ||
		(secret != nil && change.GetKey().GetID() != secret.GetTargetMessageKey().GetID()) {
		return
	}
	attachEditContent(out, change)
}
