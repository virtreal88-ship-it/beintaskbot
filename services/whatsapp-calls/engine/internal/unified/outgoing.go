package unified

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"errors"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

// Outgoing is private Worker-to-engine data, never a browser authorization.
// Scope is checked against the process binding before calling Deliver.
type Outgoing struct {
	Scope                 Scope           `json:"scope"`
	Operation             string          `json:"operation"`
	Chat                  string          `json:"chat"`
	Kind                  string          `json:"kind"`
	Text                  string          `json:"text"`
	MIME                  string          `json:"mime"`
	Name                  string          `json:"name"`
	Data                  []byte          `json:"data"`
	VerifyGroupMembership bool            `json:"verify_group_membership,omitempty"`
	QuoteEvent            string          `json:"quote_event,omitempty"`
	QuoteID               string          `json:"quote_id,omitempty"`
	LegacyQuote           *LegacyQuote    `json:"legacy_quote,omitempty"`
	Reaction              *ReactionTarget `json:"reaction,omitempty"`
	Edit                  *EditTarget     `json:"edit,omitempty"`
	Delete                *DeleteTarget   `json:"delete,omitempty"`
}

type EditTarget struct {
	ID         string `json:"id"`
	OriginalMS int64  `json:"original_ms"`
}

type DeleteTarget struct {
	ID         string `json:"id"`
	OriginalMS int64  `json:"original_ms"`
}

// LegacyQuote comes only from the authenticated Worker's exact historical row.
// It carries no media key, URL or caller-selected protobuf.
type LegacyQuote struct {
	Chat       string `json:"chat"`
	ProviderID string `json:"provider_id"`
	Sender     string `json:"sender"`
	Text       string `json:"text"`
}

type MessageClient interface {
	GenerateMessageID() types.MessageID
	Upload(context.Context, []byte, whatsmeow.MediaType) (whatsmeow.UploadResponse, error)
	BuildEdit(types.JID, types.MessageID, *waE2E.Message) *waE2E.Message
	BuildRevoke(types.JID, types.JID, types.MessageID) *waE2E.Message
	SendMessage(context.Context, types.JID, *waE2E.Message, ...whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error)
}

var messagePeer = regexp.MustCompile(`^[1-9][0-9]{4,29}(?:-[0-9]{5,20})?@(s\.whatsapp\.net|lid|g\.us)$`)
var messageActionOperation = regexp.MustCompile(`^(edit|delete):[1-9][0-9]{0,18}:[0-9a-f-]{36}$`)

func (r Outgoing) Validate(scope Scope) error {
	if r.Scope != scope || r.Operation == "" || len(r.Operation) > 128 || !messagePeer.MatchString(r.Chat) ||
		(r.VerifyGroupMembership && !strings.HasSuffix(r.Chat, "@g.us")) ||
		!utf8.ValidString(r.Text) || len(r.Text) > 65536 || strings.ContainsRune(r.Text, 0) || len(r.Name) > 200 || strings.ContainsAny(r.Name, "/\\\x00\r\n") {
		return ErrConfiguration
	}
	if r.Kind == "reaction" {
		if r.Reaction == nil || r.Edit != nil || r.Delete != nil || !r.Reaction.valid(r.Chat) || !validReactionText(r.Text) ||
			len(r.Data) != 0 || r.MIME != "" || r.Name != "" ||
			r.QuoteEvent != "" || r.QuoteID != "" || r.LegacyQuote != nil {
			return ErrConfiguration
		}
		return nil
	}
	if r.Reaction != nil {
		return ErrConfiguration
	}
	if (r.Kind == "edit") != (r.Edit != nil) || (r.Kind == "delete") != (r.Delete != nil) ||
		(r.Edit != nil && r.Delete != nil) {
		return ErrConfiguration
	}
	if r.Edit != nil && (!controlMessageID.MatchString(r.Edit.ID) || r.Edit.OriginalMS <= 0 ||
		!messageActionOperation.MatchString(r.Operation) || !strings.HasPrefix(r.Operation, "edit:") ||
		len(r.Text) > 32000 || len(r.Data) != 0 || r.MIME != "" || r.Name != "" ||
		r.QuoteEvent != "" || r.QuoteID != "" || r.LegacyQuote != nil) {
		return ErrConfiguration
	}
	if r.Delete != nil && (!controlMessageID.MatchString(r.Delete.ID) || r.Delete.OriginalMS <= 0 ||
		!messageActionOperation.MatchString(r.Operation) || !strings.HasPrefix(r.Operation, "delete:") ||
		r.Text != "" || len(r.Data) != 0 || r.MIME != "" || r.Name != "" ||
		r.QuoteEvent != "" || r.QuoteID != "" || r.LegacyQuote != nil) {
		return ErrConfiguration
	}
	if ((r.QuoteEvent == "" && r.LegacyQuote == nil) != (r.QuoteID == "")) || (r.QuoteEvent != "" && (len(r.QuoteEvent) != 64 || r.LegacyQuote != nil)) {
		return ErrConfiguration
	}
	if q := r.LegacyQuote; q != nil {
		if q.Chat != r.Chat || q.ProviderID != r.QuoteID || len(q.ProviderID) < 5 || len(q.ProviderID) > 200 || !messagePeer.MatchString(q.Sender) || strings.HasSuffix(q.Sender, "@g.us") || !utf8.ValidString(q.Text) || len(q.Text) == 0 || len(q.Text) > 65536 || strings.ContainsRune(q.Text, 0) {
			return ErrConfiguration
		}
		if !strings.HasSuffix(r.Chat, "@g.us") && q.Sender != r.Chat && q.Sender != scope.Account {
			return ErrConfiguration
		}
	}
	if r.Kind == "delete" {
		return nil
	}
	if r.Kind == "text" || r.Kind == "edit" {
		if strings.TrimSpace(r.Text) == "" || len(r.Data) != 0 || r.MIME != "" || r.Name != "" {
			return ErrConfiguration
		}
		return nil
	}
	limit := MaxMediaBytes
	if r.Kind == "video" && r.MIME == "video/mp4" {
		limit = MaxVideoBytes
	}
	if len(r.Data) == 0 || len(r.Data) > limit {
		return ErrMediaSize
	}
	if (r.Kind == "voice" || r.Kind == "audio") && r.Text != "" {
		return ErrConfiguration
	}
	allowed := map[string]map[string]bool{
		"image": {"image/jpeg": true, "image/png": true, "image/webp": true},
		"audio": {"audio/ogg": true, "audio/mpeg": true, "audio/mp4": true},
		"voice": {"audio/ogg": true}, "video": {"video/mp4": true},
		"document": {"application/pdf": true, "text/plain": true, "application/vnd.openxmlformats-officedocument.wordprocessingml.document": true},
	}
	if !allowed[r.Kind][r.MIME] {
		return ErrMediaRestricted
	}
	return nil
}

// Upload AND Send are inside one durable operation. A timeout at either step
// remains unknown; repeating the request cannot upload/send a second time.
func (j *Journal) Deliver(ctx context.Context, client MessageClient, request Outgoing, lids store.LIDStore, groupCheck ...func(context.Context) error) (Result, error) {
	if client == nil || (request.VerifyGroupMembership && (len(groupCheck) != 1 || groupCheck[0] == nil)) {
		return Result{}, ErrConfiguration
	}
	if err := request.Validate(j.binding); err != nil {
		return Result{}, err
	}
	var seconds uint32
	if request.Kind == "voice" {
		var err error
		seconds, err = pttSeconds(request.Data)
		if err != nil {
			return Result{}, err
		}
	}
	chat, err := types.ParseJID(request.Chat)
	if err != nil || chat.Device != 0 {
		return Result{}, ErrConfiguration
	}
	if request.Edit != nil {
		if result, found, lookupErr := j.existingAction(ctx, request.Operation, request.Chat); found || lookupErr != nil {
			return result, lookupErr
		}
		age := time.Since(time.UnixMilli(request.Edit.OriginalMS))
		if age < 0 || age >= 15*time.Minute {
			return Result{}, ErrEditExpired
		}
		if err := j.ownSentMessage(ctx, request.Chat, request.Edit.ID); err != nil {
			return Result{}, ErrActionOriginal
		}
	}
	if request.Delete != nil {
		if result, found, lookupErr := j.existingAction(ctx, request.Operation, request.Chat); found || lookupErr != nil {
			return result, lookupErr
		}
		age := time.Since(time.UnixMilli(request.Delete.OriginalMS))
		if age < 0 || age >= 48*time.Hour {
			return Result{}, ErrDeleteExpired
		}
		if err := j.ownSentMessage(ctx, request.Chat, request.Delete.ID); err != nil {
			return Result{}, ErrActionOriginal
		}
	}
	var quote *waE2E.ContextInfo
	if q := request.LegacyQuote; q != nil {
		quote = &waE2E.ContextInfo{StanzaID: &q.ProviderID, Participant: &q.Sender, RemoteJID: &q.Chat, QuotedMessage: &waE2E.Message{Conversation: &q.Text}}
	}
	if request.QuoteEvent != "" {
		event, err := j.Event(ctx, request.QuoteEvent)
		var evidence Envelope
		if err != nil || json.Unmarshal(event.Payload, &evidence) != nil || evidence.Kind != "message" || evidence.Variant != "" || !quoteChatMatches(ctx, evidence, request.Chat, lids) || evidence.ProviderID != request.QuoteID || !messagePeer.MatchString(evidence.Sender) || strings.HasSuffix(evidence.Sender, "@g.us") {
			return Result{}, j.rejectQuote(ctx, request.Operation)
		}
		original := &waE2E.Message{}
		if len(evidence.Proto) == 0 || proto.Unmarshal(evidence.Proto, original) != nil {
			return Result{}, j.rejectQuote(ctx, request.Operation)
		}
		quote = &waE2E.ContextInfo{StanzaID: &evidence.ProviderID, Participant: &evidence.Sender, RemoteJID: &request.Chat, QuotedMessage: original}
	}
	fingerprint := request
	fingerprint.Data = nil
	// Generation is an ownership fence, not a new send identity.
	fingerprint.Scope.Generation = ""
	digest := sha256.Sum256(request.Data)
	body, _ := json.Marshal(struct {
		Request Outgoing
		Digest  [32]byte
	}{fingerprint, digest})
	return j.Send(ctx, request.Operation, request.Chat, body, client.GenerateMessageID, func(ctx context.Context, id string) error {
		msg := &waE2E.Message{Conversation: proto.String(request.Text)}
		if quote != nil {
			msg = &waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{Text: &request.Text, ContextInfo: quote}}
		}
		if request.Edit != nil {
			msg = client.BuildEdit(chat, types.MessageID(request.Edit.ID), &waE2E.Message{Conversation: proto.String(request.Text)})
		}
		if request.Delete != nil {
			msg = client.BuildRevoke(chat, types.EmptyJID, types.MessageID(request.Delete.ID))
		}
		if target := request.Reaction; target != nil {
			msg = &waE2E.Message{ReactionMessage: &waE2E.ReactionMessage{
				Key: &waCommon.MessageKey{
					RemoteJID: proto.String(request.Chat), ID: proto.String(target.ID),
					FromMe: proto.Bool(target.FromMe), Participant: optionalParticipant(target.Participant),
				},
				Text: proto.String(request.Text), SenderTimestampMS: proto.Int64(time.Now().UnixMilli()),
			}}
		} else if request.Kind != "text" && request.Kind != "edit" && request.Kind != "delete" {
			mediaType := whatsmeow.MediaDocument
			switch request.Kind {
			case "image":
				mediaType = whatsmeow.MediaImage
			case "audio", "voice":
				mediaType = whatsmeow.MediaAudio
			case "video":
				mediaType = whatsmeow.MediaVideo
			}
			uploadCtx := ctx
			if request.Kind == "video" && len(request.Data) > MaxMediaBytes {
				uploadCtx = WithVideoTransfer(ctx)
			}
			upload, err := client.Upload(uploadCtx, request.Data, mediaType)
			if err != nil {
				return ErrUnknown
			}
			switch request.Kind {
			case "image":
				msg = &waE2E.Message{ImageMessage: &waE2E.ImageMessage{URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileSHA256: upload.FileSHA256, FileEncSHA256: upload.FileEncSHA256, FileLength: &upload.FileLength, Mimetype: &request.MIME, Caption: &request.Text}}
			case "audio", "voice":
				ptt := request.Kind == "voice"
				mime := request.MIME
				if ptt {
					// The Worker has already converted the recording to Ogg Opus.
					// Keep the validated request and journal fingerprint unchanged.
					mime = "audio/ogg; codecs=opus"
				}
				msg = &waE2E.Message{AudioMessage: &waE2E.AudioMessage{URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileSHA256: upload.FileSHA256, FileEncSHA256: upload.FileEncSHA256, FileLength: &upload.FileLength, Mimetype: &mime, PTT: proto.Bool(ptt)}}
				if ptt {
					msg.AudioMessage.Seconds = proto.Uint32(seconds)
				}
			case "video":
				msg = &waE2E.Message{VideoMessage: &waE2E.VideoMessage{URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileSHA256: upload.FileSHA256, FileEncSHA256: upload.FileEncSHA256, FileLength: &upload.FileLength, Mimetype: &request.MIME, Caption: &request.Text}}
			default:
				msg = &waE2E.Message{DocumentMessage: &waE2E.DocumentMessage{URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileSHA256: upload.FileSHA256, FileEncSHA256: upload.FileEncSHA256, FileLength: &upload.FileLength, Mimetype: &request.MIME, FileName: &request.Name, Caption: &request.Text}}
			}
		}
		if quote != nil {
			if m := msg.GetImageMessage(); m != nil {
				m.ContextInfo = quote
			}
			if m := msg.GetAudioMessage(); m != nil {
				m.ContextInfo = quote
			}
			if m := msg.GetVideoMessage(); m != nil {
				m.ContextInfo = quote
			}
			if m := msg.GetDocumentMessage(); m != nil {
				m.ContextInfo = quote
			}
		}
		if request.VerifyGroupMembership {
			if err := groupCheck[0](ctx); err != nil {
				return err
			}
		}
		response, err := client.SendMessage(ctx, chat, msg, whatsmeow.SendRequestExtra{ID: id})
		if err != nil || response.ID != id {
			return ErrUnknown
		}
		if request.Edit != nil || request.Delete != nil {
			stamp := response.Timestamp
			if stamp.IsZero() {
				stamp = time.Now()
			}
			sender, _ := types.ParseJID(j.binding.Account)
			identity, envelope, retained, err := MessageEnvelope(&events.Message{
				Info:    types.MessageInfo{MessageSource: types.MessageSource{Chat: chat, Sender: sender, IsFromMe: true}, ID: id, Timestamp: stamp},
				Message: msg,
			})
			if err != nil || !retained {
				return ErrUnknown
			}
			envelope.AppDispatch = true
			if request.Edit != nil && envelope.Edit != nil {
				envelope.Edit.Operation = request.Operation
			}
			if request.Delete != nil && envelope.Revoke != nil {
				envelope.Revoke.Operation = request.Operation
			}
			payload, err := json.Marshal(envelope)
			if err != nil {
				return ErrUnknown
			}
			_, err = j.Append(ctx, "app-action:"+identity, payload)
			return err
		}
		if request.Kind == "text" {
			return nil
		}
		// SendMessage does not emit a Message event for our own device. Retain
		// the acknowledged protobuf through the same encrypted event path used
		// by incoming messages; media keys must never enter the public receipt.
		// If persistence fails, keep the operation unknown, never resend it.
		stamp := response.Timestamp
		if stamp.IsZero() {
			stamp = time.Now()
		}
		sender, _ := types.ParseJID(j.binding.Account)
		identity, envelope, retained, err := MessageEnvelope(&events.Message{
			Info:    types.MessageInfo{MessageSource: types.MessageSource{Chat: chat, Sender: sender, IsFromMe: true}, ID: id, Timestamp: stamp},
			Message: msg,
		})
		if err != nil || !retained {
			return ErrUnknown
		}
		envelope.AppDispatch = true
		payload, err := json.Marshal(envelope)
		if err != nil {
			return ErrUnknown
		}
		_, err = j.Append(ctx, identity, payload)
		return err
	})
}

func (j *Journal) existingAction(ctx context.Context, operation, peer string) (Result, bool, error) {
	result, err := j.Lookup(ctx, operation, peer)
	if errors.Is(err, sql.ErrNoRows) {
		return Result{}, false, nil
	}
	if err != nil {
		return Result{}, false, err
	}
	if result.State == "unknown" {
		return result, true, ErrUnknown
	}
	return result, true, nil
}

func (j *Journal) ownSentMessage(ctx context.Context, peer, providerID string) error {
	var state string
	err := j.db.QueryRowContext(ctx, `SELECT state FROM unified_sends WHERE scope=? AND provider_id=? AND peer_hash=?`,
		j.operationScope, providerID, j.digestForScope(j.operationScope, "peer", peer)).Scan(&state)
	if err != nil || state != "accepted" {
		return ErrActionOriginal
	}
	return nil
}

func quoteChatMatches(ctx context.Context, e Envelope, chat string, lids store.LIDStore) bool {
	if e.Chat == chat {
		return true
	}
	if !messagePeer.MatchString(e.Chat) || strings.HasSuffix(e.Chat, "@g.us") || strings.HasSuffix(chat, "@g.us") {
		return false
	}
	alternate := e.RecipientAlt
	if !e.FromMe {
		if e.Sender != e.Chat {
			return false
		}
		alternate = e.SenderAlt
	}
	original, err := types.ParseJID(e.Chat)
	if err != nil {
		return false
	}
	target, err := types.ParseJID(chat)
	if err != nil {
		return false
	}
	lid, phone := original, target
	if target.Server == types.HiddenUserServer {
		lid, phone = target, original
	}
	if lid.Server != types.HiddenUserServer || phone.Server != types.DefaultUserServer {
		return false
	}
	if alternate != "" {
		return alternate == chat
	}
	if lids == nil {
		return false
	}
	// Old encrypted events may predate alternate-address capture. Only the
	// paired device's stored bidirectional WhatsApp mapping can fill that gap.
	savedPhone, err := lids.GetPNForLID(ctx, lid)
	if err != nil || savedPhone != phone {
		return false
	}
	savedLID, err := lids.GetLIDForPN(ctx, phone)
	return err == nil && savedLID == lid
}
