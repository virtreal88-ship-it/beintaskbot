//go:build unifiedlab

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"mime"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
	"wacalls/internal/unified"
)

const labUploadLimit = 64 << 10

type labMediaUpload struct {
	Operation string `json:"operation"`
	Chat      string `json:"chat"`
	Kind      string `json:"kind"`
	MIME      string `json:"mime"`
	Name      string `json:"name"`
	Data      []byte `json:"data"`
}

func validateLabMedia(request labMediaUpload) bool {
	if len(request.Data) == 0 || len(request.Data) > labUploadLimit || len(request.Name) > 120 || strings.ContainsAny(request.Name, "/\\\x00\r\n") {
		return false
	}
	switch request.Kind {
	case "image":
		if request.MIME != "image/png" && request.MIME != "image/jpeg" {
			return false
		}
		if http.DetectContentType(request.Data) != request.MIME {
			return false
		}
		config, _, err := image.DecodeConfig(bytes.NewReader(request.Data))
		return err == nil && config.Width > 0 && config.Height > 0 && config.Width <= 1024 && config.Height <= 1024
	case "document":
		return (request.MIME == "text/plain" && utf8.Valid(request.Data) && !bytes.ContainsRune(request.Data, 0)) || (request.MIME == "application/pdf" && bytes.HasPrefix(request.Data, []byte("%PDF-")))
	case "voice":
		// Lab only: allow a small Ogg Opus fixture. This is not the production
		// duration/decoder admission policy, which remains in the Worker adapter.
		if request.MIME != "audio/ogg" || len(request.Data) < 28 || !bytes.HasPrefix(request.Data, []byte("OggS")) || request.Data[4] != 0 {
			return false
		}
		start := 27 + int(request.Data[26])
		return len(request.Data) >= start+19 && bytes.Equal(request.Data[start:start+8], []byte("OpusHead"))
	}
	return false
}

func labUploadMessage(request labMediaUpload, upload whatsmeow.UploadResponse) *waE2E.Message {
	switch request.Kind {
	case "image":
		return &waE2E.Message{ImageMessage: &waE2E.ImageMessage{URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileSHA256: upload.FileSHA256, FileEncSHA256: upload.FileEncSHA256, FileLength: &upload.FileLength, Mimetype: &request.MIME}}
	case "voice":
		return &waE2E.Message{AudioMessage: &waE2E.AudioMessage{URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileSHA256: upload.FileSHA256, FileEncSHA256: upload.FileEncSHA256, FileLength: &upload.FileLength, Mimetype: proto.String("audio/ogg; codecs=opus"), PTT: proto.Bool(true)}}
	default:
		return &waE2E.Message{DocumentMessage: &waE2E.DocumentMessage{URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileSHA256: upload.FileSHA256, FileEncSHA256: upload.FileEncSHA256, FileLength: &upload.FileLength, Mimetype: &request.MIME, FileName: &request.Name}}
	}
}

func labMediaFacts(message *waE2E.Message) (kind, mime string, size uint64, name string) {
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
	}
	return
}

func (s *server) labReady(m *messageLab) (*Session, bool) {
	session, ok := s.sessions.Get(m.cfg.SessionID)
	return session, ok && m.matches(session) && session.transportReady()
}

func (s *server) registerLabMedia(mux *http.ServeMux, m *messageLab) {
	mux.HandleFunc("POST /api/unified-lab/media", func(w http.ResponseWriter, r *http.Request) {
		var request labMediaUpload
		decoder := json.NewDecoder(io.LimitReader(r.Body, 128<<10))
		decoder.DisallowUnknownFields()
		if decoder.Decode(&request) != nil || decoder.Decode(new(any)) != io.EOF || !validateLabMedia(request) {
			writeJSON(w, 400, map[string]string{"error": "invalid_test_media"})
			return
		}
		chat, err := types.ParseJID(request.Chat)
		if err != nil || !m.allows(chat) {
			writeJSON(w, 403, map[string]string{"error": "test_peer_denied"})
			return
		}
		session, ready := s.labReady(m)
		if !ready {
			writeJSON(w, 409, map[string]string{"error": "test_session_unavailable"})
			return
		}
		body, _ := json.Marshal(request)
		ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
		defer cancel()
		result, err := m.journal.Send(ctx, request.Operation, request.Chat, body, session.client.GenerateMessageID, func(ctx context.Context, id string) error {
			mediaType := whatsmeow.MediaDocument
			if request.Kind == "image" {
				mediaType = whatsmeow.MediaImage
			} else if request.Kind == "voice" {
				mediaType = whatsmeow.MediaAudio
			}
			upload, err := session.client.Upload(ctx, request.Data, mediaType)
			if err != nil {
				return unified.ErrUnknown
			}
			response, err := session.client.SendMessage(ctx, chat, labUploadMessage(request, upload), whatsmeow.SendRequestExtra{ID: id})
			if err != nil || response.ID != id {
				return unified.ErrUnknown
			}
			return nil
		})
		code := 200
		if err != nil {
			code = 503
		}
		writeJSON(w, code, map[string]any{"result": result, "confirmed": err == nil})
	})
	mux.HandleFunc("GET /api/unified-lab/media/{id}", func(w http.ResponseWriter, r *http.Request) {
		event, err := m.journal.Event(r.Context(), r.PathValue("id"))
		if err != nil {
			writeJSON(w, 404, map[string]string{"error": "media_not_found"})
			return
		}
		var envelope labEnvelope
		if json.Unmarshal(event.Payload, &envelope) != nil || envelope.Kind != "message" {
			writeJSON(w, 404, map[string]string{"error": "media_not_found"})
			return
		}
		chat, err := types.ParseJID(envelope.Chat)
		if err != nil || !m.allows(chat) {
			writeJSON(w, 404, map[string]string{"error": "media_not_found"})
			return
		}
		var message waE2E.Message
		if proto.Unmarshal(envelope.Proto, &message) != nil {
			writeJSON(w, 404, map[string]string{"error": "media_not_found"})
			return
		}
		kind, contentType, size, name := labMediaFacts(&message)
		if kind == "" || size == 0 || size > unified.MaxMediaBytes {
			writeJSON(w, 413, map[string]string{"error": "media_size_or_type"})
			return
		}
		session, ready := s.labReady(m)
		if !ready {
			writeJSON(w, 409, map[string]string{"error": "test_session_unavailable"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
		defer cancel()
		data, err := session.client.DownloadAny(ctx, &message)
		if err != nil || uint64(len(data)) != size || len(data) > unified.MaxMediaBytes {
			writeJSON(w, 502, map[string]string{"error": "media_unavailable"})
			return
		}
		// Never execute a document or remote markup in the authenticated lab origin.
		if kind == "document" {
			contentType = "application/octet-stream"
		}
		if strings.ContainsAny(contentType, "\r\n") || len(contentType) > 120 {
			contentType = "application/octet-stream"
		}
		if len(name) > 120 || strings.ContainsAny(name, "/\\\r\n\x00") {
			name = "test-attachment"
		}
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Content-Security-Policy", "sandbox; default-src 'none'")
		w.Header().Set("Content-Type", contentType)
		w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": name}))
		w.Write(data)
	})
	mux.HandleFunc("GET /api/unified-lab/groups/{chat}", func(w http.ResponseWriter, r *http.Request) {
		chat, err := types.ParseJID(r.PathValue("chat"))
		if err != nil || chat.Server != types.GroupServer || !m.allows(chat) {
			writeJSON(w, 403, map[string]string{"error": "test_group_denied"})
			return
		}
		session, ready := s.labReady(m)
		if !ready {
			writeJSON(w, 409, map[string]string{"error": "test_session_unavailable"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
		defer cancel()
		group, err := session.client.GetGroupInfo(ctx, chat)
		if err != nil || group == nil || group.JID != chat {
			writeJSON(w, 502, map[string]string{"error": "group_unavailable"})
			return
		}
		participants := make([]map[string]any, 0, len(group.Participants))
		for _, p := range group.Participants {
			participants = append(participants, map[string]any{"jid": p.JID.String(), "phone": p.PhoneNumber.String(), "lid": p.LID.String(), "admin": p.IsAdmin})
		}
		writeJSON(w, 200, map[string]any{"chat": chat.String(), "name": group.Name, "participants": participants})
	})
}
