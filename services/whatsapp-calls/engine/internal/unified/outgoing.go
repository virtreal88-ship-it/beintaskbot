package unified

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"regexp"
	"strings"
	"unicode/utf8"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
)

// Outgoing is private Worker-to-engine data, never a browser authorization.
// Scope is checked against the process binding before calling Deliver.
type Outgoing struct {
	Scope      Scope  `json:"scope"`
	Operation  string `json:"operation"`
	Chat       string `json:"chat"`
	Kind       string `json:"kind"`
	Text       string `json:"text"`
	MIME       string `json:"mime"`
	Name       string `json:"name"`
	Data       []byte `json:"data"`
	QuoteEvent string `json:"quote_event,omitempty"`
	QuoteID    string `json:"quote_id,omitempty"`
}

type MessageClient interface {
	GenerateMessageID() types.MessageID
	Upload(context.Context, []byte, whatsmeow.MediaType) (whatsmeow.UploadResponse, error)
	SendMessage(context.Context, types.JID, *waE2E.Message, ...whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error)
}

var messagePeer = regexp.MustCompile(`^[1-9][0-9]{4,29}(?:-[0-9]{5,20})?@(s\.whatsapp\.net|lid|g\.us)$`)

func (r Outgoing) Validate(scope Scope) error {
	if r.Scope != scope || r.Operation == "" || len(r.Operation) > 128 || !messagePeer.MatchString(r.Chat) ||
		!utf8.ValidString(r.Text) || len(r.Text) > 65536 || strings.ContainsRune(r.Text, 0) || len(r.Name) > 200 || strings.ContainsAny(r.Name, "/\\\x00\r\n") {
		return ErrConfiguration
	}
	if (r.QuoteEvent == "") != (r.QuoteID == "") || (r.QuoteEvent != "" && (len(r.QuoteEvent) != 64 || r.Kind != "text")) {
		return ErrConfiguration
	}
	if r.Kind == "text" {
		if strings.TrimSpace(r.Text) == "" || len(r.Data) != 0 || r.MIME != "" || r.Name != "" {
			return ErrConfiguration
		}
		return nil
	}
	if len(r.Data) == 0 || len(r.Data) > MaxMediaBytes {
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
func (j *Journal) Deliver(ctx context.Context, client MessageClient, request Outgoing) (Result, error) {
	if client == nil {
		return Result{}, ErrConfiguration
	}
	if err := request.Validate(j.binding); err != nil {
		return Result{}, err
	}
	chat, err := types.ParseJID(request.Chat)
	if err != nil || chat.Device != 0 {
		return Result{}, ErrConfiguration
	}
	var quote *waE2E.ContextInfo
	if request.QuoteEvent != "" {
		event, err := j.Event(ctx, request.QuoteEvent)
		var evidence Envelope
		if err != nil || json.Unmarshal(event.Payload, &evidence) != nil || evidence.Kind != "message" || evidence.Variant != "" || !quoteChatMatches(evidence, request.Chat) || evidence.ProviderID != request.QuoteID || !messagePeer.MatchString(evidence.Sender) || strings.HasSuffix(evidence.Sender, "@g.us") {
			return Result{}, ErrConflict
		}
		original := &waE2E.Message{}
		if len(evidence.Proto) == 0 || proto.Unmarshal(evidence.Proto, original) != nil {
			return Result{}, ErrConflict
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
		if request.Kind != "text" {
			mediaType := whatsmeow.MediaDocument
			switch request.Kind {
			case "image":
				mediaType = whatsmeow.MediaImage
			case "audio", "voice":
				mediaType = whatsmeow.MediaAudio
			case "video":
				mediaType = whatsmeow.MediaVideo
			}
			upload, err := client.Upload(ctx, request.Data, mediaType)
			if err != nil {
				return ErrUnknown
			}
			switch request.Kind {
			case "image":
				msg = &waE2E.Message{ImageMessage: &waE2E.ImageMessage{URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileSHA256: upload.FileSHA256, FileEncSHA256: upload.FileEncSHA256, FileLength: &upload.FileLength, Mimetype: &request.MIME, Caption: &request.Text}}
			case "audio", "voice":
				msg = &waE2E.Message{AudioMessage: &waE2E.AudioMessage{URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileSHA256: upload.FileSHA256, FileEncSHA256: upload.FileEncSHA256, FileLength: &upload.FileLength, Mimetype: &request.MIME, PTT: proto.Bool(request.Kind == "voice")}}
			case "video":
				msg = &waE2E.Message{VideoMessage: &waE2E.VideoMessage{URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileSHA256: upload.FileSHA256, FileEncSHA256: upload.FileEncSHA256, FileLength: &upload.FileLength, Mimetype: &request.MIME, Caption: &request.Text}}
			default:
				msg = &waE2E.Message{DocumentMessage: &waE2E.DocumentMessage{URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileSHA256: upload.FileSHA256, FileEncSHA256: upload.FileEncSHA256, FileLength: &upload.FileLength, Mimetype: &request.MIME, FileName: &request.Name, Caption: &request.Text}}
			}
		}
		response, err := client.SendMessage(ctx, chat, msg, whatsmeow.SendRequestExtra{ID: id})
		if err != nil || response.ID != id {
			return ErrUnknown
		}
		return nil
	})
}

func quoteChatMatches(e Envelope, chat string) bool {
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
	return alternate == chat && ((strings.HasSuffix(e.Chat, "@lid") && strings.HasSuffix(chat, "@s.whatsapp.net")) || (strings.HasSuffix(e.Chat, "@s.whatsapp.net") && strings.HasSuffix(chat, "@lid")))
}
