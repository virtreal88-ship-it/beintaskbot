package unified

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"regexp"
	"strings"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

var controlMessageID = regexp.MustCompile(`^[A-Za-z0-9_-]{5,200}$`)

// Envelope is private engine-to-Worker evidence. Proto may contain media keys;
// only the encrypted journal/inbox may retain it, never CRM raw JSON or logs.
// The chat allowlist/source binding is checked by the caller BEFORE extraction.
type Envelope struct {
	Kind                   string          `json:"kind"`
	Chat                   string          `json:"chat"`
	Sender                 string          `json:"sender,omitempty"`
	SenderAlt              string          `json:"sender_alt,omitempty"`
	RecipientAlt           string          `json:"recipient_alt,omitempty"`
	ProviderID             string          `json:"provider_id"`
	FromMe                 bool            `json:"from_me"`
	AppDispatch            bool            `json:"app_dispatch,omitempty"`
	Timestamp              int64           `json:"timestamp"`
	Text                   string          `json:"text,omitempty"`
	Status                 string          `json:"status,omitempty"`
	Proto                  []byte          `json:"proto,omitempty"`
	MediaKind              string          `json:"media_kind,omitempty"`
	MediaMime              string          `json:"media_mime,omitempty"`
	MediaSize              uint64          `json:"media_size,omitempty"`
	FileName               string          `json:"file_name,omitempty"`
	MessageType            string          `json:"message_type,omitempty"`
	PushName               string          `json:"push_name,omitempty"`
	QuoteID                string          `json:"quote_id,omitempty"`
	QuoteChat              string          `json:"quote_chat,omitempty"`
	QuoteSender            string          `json:"quote_sender,omitempty"`
	Variant                string          `json:"variant,omitempty"`
	Reaction               *ReactionTarget `json:"reaction,omitempty"`
	ReactionParticipantAlt string          `json:"reaction_participant_alt,omitempty"`
	Edit                   *MessageEdit    `json:"edit,omitempty"`
	Revoke                 *MessageRevoke  `json:"revoke,omitempty"`
}

// MessageEdit and MessageRevoke contain only the exact target identity. The
// encrypted journal retains the original protocol message; CRM never receives
// protobuf payloads or media keys.
type MessageEdit struct {
	ID        string `json:"id"`
	Operation string `json:"operation,omitempty"`
}

type MessageRevoke struct {
	ID          string `json:"id"`
	Chat        string `json:"chat"`
	FromMe      bool   `json:"from_me"`
	Participant string `json:"participant,omitempty"`
	Operation   string `json:"operation,omitempty"`
}

// MessageEnvelope retains the original lab event identity across rollout. New
// metadata must not turn provider redelivery into another message. A returned
// retained=false is intentional ephemeral/view-once exclusion, not DB success.
func MessageEnvelope(event *events.Message) (identity string, out Envelope, retained bool, err error) {
	if event == nil || event.Message == nil || event.Info.ID == "" || event.Info.Chat.IsEmpty() {
		return "", out, false, ErrConfiguration
	}
	if event.IsViewOnce || event.IsViewOnceV2 || event.IsViewOnceV2Extension || event.IsEphemeral {
		return "", out, false, nil
	}
	data, err := proto.MarshalOptions{Deterministic: true}.Marshal(event.Message)
	if err != nil {
		return "", out, false, ErrConfiguration
	}
	out = Envelope{Kind: "message", Chat: event.Info.Chat.String(), Sender: event.Info.Sender.String(),
		SenderAlt: event.Info.SenderAlt.String(), RecipientAlt: event.Info.RecipientAlt.String(),
		ProviderID: event.Info.ID, FromMe: event.Info.IsFromMe, Timestamp: event.Info.Timestamp.UnixMilli(),
		Text: event.Message.GetConversation(), Proto: data, PushName: event.Info.PushName, MessageType: "unsupported"}
	out.MediaKind, out.MediaMime, out.MediaSize, out.FileName = MediaFacts(event.Message)
	var context *waE2E.ContextInfo
	switch {
	case event.Message.GetConversation() != "":
		out.MessageType = "text"
	case event.Message.GetExtendedTextMessage() != nil:
		m := event.Message.GetExtendedTextMessage()
		out.Text, context, out.MessageType = m.GetText(), m.GetContextInfo(), "text"
	case event.Message.GetImageMessage() != nil:
		m := event.Message.GetImageMessage()
		out.Text, context, out.MessageType = m.GetCaption(), m.GetContextInfo(), "image"
	case event.Message.GetVideoMessage() != nil:
		m := event.Message.GetVideoMessage()
		out.Text, context, out.MessageType = m.GetCaption(), m.GetContextInfo(), "video"
	case event.Message.GetAudioMessage() != nil:
		m := event.Message.GetAudioMessage()
		context, out.MessageType = m.GetContextInfo(), "audio"
	case event.Message.GetDocumentMessage() != nil:
		m := event.Message.GetDocumentMessage()
		out.Text, context, out.MessageType = m.GetCaption(), m.GetContextInfo(), "document"
	case event.Message.GetStickerMessage() != nil:
		m := event.Message.GetStickerMessage()
		context, out.MessageType = m.GetContextInfo(), "sticker"
	}
	if context != nil {
		out.QuoteID, out.QuoteChat, out.QuoteSender = context.GetStanzaID(), context.GetRemoteJID(), context.GetParticipant()
	}
	variant := "original"
	// One group stanza can dispatch a sender-key distribution and the actual
	// content separately, with the same chat/sender/provider ID. A key-only
	// envelope must not occupy the original message's immutable journal slot.
	// If content accompanies the key, retain its existing original identity.
	keyOnly := out.MessageType == "unsupported" &&
		(event.Message.GetSenderKeyDistributionMessage() != nil || event.Message.GetFastRatchetKeySenderKeyDistributionMessage() != nil)
	secretEdit := event.Message.GetSecretEncryptedMessage().GetSecretEncType() == waE2E.SecretEncryptedMessage_MESSAGE_EDIT
	if secretEdit || event.IsEdit || event.Message.GetEditedMessage() != nil || event.Message.GetProtocolMessage() != nil || event.Message.GetReactionMessage() != nil || keyOnly {
		hash := sha256.Sum256(data)
		variant = hex.EncodeToString(hash[:])
		// Do not project an edit/reaction/protocol payload as a fresh user turn.
		out.Variant, out.MessageType = "control", "unsupported"
	}
	if out.Variant == "control" {
		observeMessageControl(event, &out)
	}
	if reaction := event.Message.GetReactionMessage(); reaction != nil && !event.IsEdit && out.FromMe {
		key := reaction.GetKey()
		target := &ReactionTarget{ID: key.GetID(), FromMe: key.GetFromMe()}
		if !target.FromMe && event.Info.Chat.Server == types.GroupServer {
			participant, err := types.ParseJID(key.GetParticipant())
			if err == nil {
				target.Participant = participant.ToNonAD().String()
			}
		}
		targetChat := key.GetRemoteJID()
		if (targetChat == out.Chat || reactionChatAlias(event.Info.Chat, event.Info.RecipientAlt, targetChat)) &&
			key.FromMe != nil && target.valid(targetChat) && validReactionText(reaction.GetText()) {
			out.Chat, out.Reaction, out.Text = targetChat, target, reaction.GetText()
			if stamp := reaction.GetSenderTimestampMS(); stamp > 0 {
				out.Timestamp = stamp
			}
		}
	}
	key, _ := json.Marshal([]any{"message", out.Chat, out.Sender, out.FromMe, out.ProviderID, variant})
	return string(key), out, true, nil
}

func observeMessageControl(event *events.Message, out *Envelope) {
	if event == nil || event.Message == nil || out == nil || out.Variant != "control" {
		return
	}
	if change := event.Message.GetProtocolMessage(); change != nil {
		if change.Type != nil && change.GetType() == waE2E.ProtocolMessage_REVOKE {
			key := change.GetKey()
			if key == nil || key.FromMe == nil || !validProviderID(key.GetID()) || !key.GetFromMe() {
				return
			}
			// The key is relative to its sender. Only accept self-revokes.
			// In a DM the authenticated outer chat identifies the other party,
			// while its inner key can identify this device's account.
			chat, sender := event.Info.Chat, event.Info.Sender.ToNonAD()
			participant := ""
			if out.FromMe {
				if key.GetRemoteJID() != out.Chat && !reactionChatAlias(chat, event.Info.RecipientAlt, key.GetRemoteJID()) {
					return
				}
			} else if chat.Server == types.GroupServer {
				if key.GetRemoteJID() != out.Chat || !reactionUser(sender) {
					return
				}
				participant = sender.String()
			} else if !reactionUser(chat) || (sender != chat && event.Info.SenderAlt.ToNonAD() != chat) {
				return
			}
			out.Revoke = &MessageRevoke{ID: key.GetID(), Chat: out.Chat, FromMe: out.FromMe, Participant: participant}
			return
		}
	}
	change := editProtocol(event.Message)
	key := change.GetKey()
	if !out.FromMe || change.GetType() != waE2E.ProtocolMessage_MESSAGE_EDIT ||
		key == nil || !key.GetFromMe() || !validProviderID(key.GetID()) || !messagePeer.MatchString(key.GetRemoteJID()) ||
		change.GetTimestampMS() <= 0 {
		return
	}
	target, err := types.ParseJID(key.GetRemoteJID())
	if err != nil || target.Device != 0 ||
		(target.String() != out.Chat && !reactionChatAlias(event.Info.Chat, event.Info.RecipientAlt, target.String())) {
		return
	}
	attachEditContent(out, change)
	if out.Edit != nil {
		out.Chat = target.ToNonAD().String()
	}
}

func editProtocol(message *waE2E.Message) *waE2E.ProtocolMessage {
	if wrapped := message.GetEditedMessage().GetMessage(); wrapped != nil {
		return wrapped.GetProtocolMessage()
	}
	return message.GetProtocolMessage()
}

func attachEditContent(out *Envelope, change *waE2E.ProtocolMessage) {
	content := change.GetEditedMessage()
	text := content.GetConversation()
	if extended := content.GetExtendedTextMessage(); extended != nil {
		text = extended.GetText()
	}
	if strings.TrimSpace(text) == "" || (out.FromMe && len(text) > 32000) || strings.ContainsRune(text, 0) {
		return
	}
	out.Timestamp, out.Text = change.GetTimestampMS(), text
	out.Edit = &MessageEdit{ID: change.GetKey().GetID()}
}

func validProviderID(value string) bool {
	return controlMessageID.MatchString(value)
}

func MediaFacts(message *waE2E.Message) (kind, mime string, size uint64, name string) {
	switch {
	case message.GetImageMessage() != nil:
		m := message.GetImageMessage()
		return "image", m.GetMimetype(), m.GetFileLength(), "image"
	case message.GetAudioMessage() != nil:
		m := message.GetAudioMessage()
		return "audio", m.GetMimetype(), m.GetFileLength(), "audio"
	case message.GetVideoMessage() != nil:
		m := message.GetVideoMessage()
		return "video", m.GetMimetype(), m.GetFileLength(), "video"
	case message.GetDocumentMessage() != nil:
		m := message.GetDocumentMessage()
		return "document", m.GetMimetype(), m.GetFileLength(), m.GetFileName()
	case message.GetStickerMessage() != nil:
		m := message.GetStickerMessage()
		return "sticker", m.GetMimetype(), m.GetFileLength(), "sticker"
	}
	return
}
