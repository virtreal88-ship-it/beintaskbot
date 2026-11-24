package unified

import (
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"

	"go.mau.fi/whatsmeow/types"
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
