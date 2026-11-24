package unified

import (
	"context"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"

	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

// ReactionTarget identifies the original message from the linked account's perspective.
type ReactionTarget struct {
	ID          string `json:"id"`
	FromMe      bool   `json:"from_me"`
	Participant string `json:"participant,omitempty"`
}

var reactionMessageID = regexp.MustCompile(`^[A-Za-z0-9_-]{5,200}$`)

func (target ReactionTarget) valid(chat string) bool {
	if !messagePeer.MatchString(chat) || !reactionMessageID.MatchString(target.ID) {
		return false
	}
	if strings.HasSuffix(chat, "@g.us") && !target.FromMe {
		return messagePeer.MatchString(target.Participant) && !strings.HasSuffix(target.Participant, "@g.us")
	}
	return target.Participant == ""
}

func validReactionText(text string) bool {
	if len(text) > 128 || !utf8.ValidString(text) {
		return false
	}
	for _, c := range text {
		if unicode.IsControl(c) || unicode.IsSpace(c) {
			return false
		}
	}
	return true // Empty clears a reaction.
}

func optionalParticipant(participant string) *string {
	if participant == "" {
		return nil
	}
	return proto.String(participant)
}

// A linked device can use the provider-supplied alternate address for the same
// private chat. Group IDs never participate in this address conversion.
func reactionChatAlias(chat, alternate types.JID, target string) bool {
	if alternate.IsEmpty() || alternate.ToNonAD().String() != target {
		return false
	}
	return (chat.Server == types.DefaultUserServer && alternate.Server == types.HiddenUserServer) ||
		(chat.Server == types.HiddenUserServer && alternate.Server == types.DefaultUserServer)
}

// A received key uses the reacting account's perspective. The containing
// message authenticates the actual chat and the reactor's sender identity.
func attachIncomingReaction(ctx context.Context, out *Envelope, event *events.Message, device *store.Device) {
	reaction := event.Message.GetReactionMessage()
	key := reaction.GetKey()
	if event.IsEdit || key == nil || !validReactionText(reaction.GetText()) {
		return
	}
	chat, sender := event.Info.Chat, event.Info.Sender.ToNonAD()
	if !reactionUser(sender) {
		return
	}
	target := ReactionTarget{ID: key.GetID()}
	alternate := ""
	if chat.Server == types.GroupServer {
		if device == nil || key.GetRemoteJID() != out.Chat {
			return
		}
		senderAlt := event.Info.SenderAlt.ToNonAD()
		if !reactionUser(senderAlt) || senderAlt.Server == sender.Server {
			if peer := reactionAlias(ctx, sender, device); !peer.IsEmpty() {
				out.SenderAlt = peer.String()
			}
		}
		author := sender
		if !key.GetFromMe() {
			parsed, err := types.ParseJID(key.GetParticipant())
			if err != nil || !reactionUser(parsed.ToNonAD()) {
				return
			}
			author = parsed.ToNonAD()
		}
		target.FromMe = author == device.GetJID().ToNonAD() || author == device.GetLID().ToNonAD()
		if !target.FromMe {
			target.Participant = author.String()
			if peer := reactionAlias(ctx, author, device); !peer.IsEmpty() {
				alternate = peer.String()
			}
		}
	} else {
		// The inner key may name this account, as seen by the other side of
		// the DM. Bind the reaction to the authenticated outer conversation.
		if !reactionUser(chat) || (sender != chat && event.Info.SenderAlt.ToNonAD() != chat) {
			return
		}
		target.FromMe = !key.GetFromMe()
	}
	if !target.valid(out.Chat) {
		return
	}
	out.Reaction, out.ReactionParticipantAlt, out.Text = &target, alternate, reaction.GetText()
	if stamp := reaction.GetSenderTimestampMS(); stamp > 0 {
		out.Timestamp = stamp
	}
}

func reactionUser(jid types.JID) bool {
	return (jid.Server == types.DefaultUserServer || jid.Server == types.HiddenUserServer) &&
		messagePeer.MatchString(jid.String())
}

func reactionAlias(ctx context.Context, jid types.JID, device *store.Device) types.JID {
	if device.LIDs == nil {
		return types.EmptyJID
	}
	var alias types.JID
	var err error
	if jid.Server == types.HiddenUserServer {
		alias, err = device.LIDs.GetPNForLID(ctx, jid)
	} else {
		alias, err = device.LIDs.GetLIDForPN(ctx, jid)
	}
	alias = alias.ToNonAD()
	if err != nil || !reactionUser(alias) || alias.Server == jid.Server {
		return types.EmptyJID
	}
	return alias
}
