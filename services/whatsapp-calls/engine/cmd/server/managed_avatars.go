package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"regexp"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
	"wacalls/internal/unified"
)

const maxAvatarBytes = 1 << 20

var avatarPeer = regexp.MustCompile(`^(?:[1-9][0-9]{5,22}@(s\.whatsapp\.net|lid)|[0-9]{5,30}(?:-[0-9]{5,20})?@g\.us)$`)

type avatarEntry struct {
	body   []byte
	mime   string
	status int
	until  time.Time
}
type managedAvatars struct {
	mu    sync.Mutex
	cache map[string]avatarEntry
}

func avatarMIME(body []byte) string {
	mime := http.DetectContentType(body)
	switch mime {
	case "image/jpeg", "image/png", "image/gif", "image/webp":
		return mime
	}
	return ""
}

func fetchAvatar(ctx context.Context, client *whatsmeow.Client, chat types.JID) avatarEntry {
	unavailable := avatarEntry{status: 503, until: time.Now().Add(30 * time.Second)}
	info, err := client.GetProfilePictureInfo(ctx, chat, &whatsmeow.GetProfilePictureParams{Preview: true})
	if errors.Is(err, whatsmeow.ErrProfilePictureUnauthorized) || errors.Is(err, whatsmeow.ErrProfilePictureNotSet) {
		return avatarEntry{status: 404, until: time.Now().Add(5 * time.Minute)}
	}
	if err != nil || info == nil || info.URL == "" {
		return unavailable
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, info.URL, nil)
	if err != nil {
		return unavailable
	}
	request.Header.Set("Accept-Encoding", "identity")
	httpClient := unified.NewMediaHTTPClient()
	defer httpClient.CloseIdleConnections()
	response, err := httpClient.Do(request)
	if err != nil {
		return unavailable
	}
	defer response.Body.Close()
	if response.StatusCode != 200 || response.ContentLength > maxAvatarBytes {
		return unavailable
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, maxAvatarBytes+1))
	mime := avatarMIME(body)
	if err != nil || len(body) == 0 || len(body) > maxAvatarBytes || mime == "" {
		return unavailable
	}
	return avatarEntry{body: body, mime: mime, status: 200, until: time.Now().Add(time.Hour)}
}

// Independent bounded cache; no avatar network I/O on message/call handlers.
func (s *server) handleManagedAvatar(w http.ResponseWriter, r *http.Request) {
	g := s.appGateway
	if g == nil || g.cfg.Messaging == nil {
		http.Error(w, "native_messaging_disabled", 409)
		return
	}
	var request struct {
		Scope unified.Scope `json:"scope"`
		Chat  string        `json:"chat"`
	}
	d := json.NewDecoder(io.LimitReader(r.Body, 4097))
	d.DisallowUnknownFields()
	if d.Decode(&request) != nil || d.Decode(new(any)) != io.EOF || request.Scope != g.cfg.Messaging.Scope || !avatarPeer.MatchString(request.Chat) {
		http.Error(w, "native_avatar_invalid", 400)
		return
	}
	chat, err := types.ParseJID(request.Chat)
	if err != nil {
		http.Error(w, "native_avatar_invalid", 400)
		return
	}
	infos := s.sessions.infos()
	if len(infos) != 1 || infos[0].State != "open" {
		http.Error(w, "source_unavailable", 503)
		return
	}
	sess, ok := s.sessions.Get(infos[0].ID)
	if !ok || !sess.transportReady() || sess.client.Store.ID == nil || sess.client.Store.ID.ToNonAD().String() != g.cfg.Account {
		http.Error(w, "source_identity_mismatch", 503)
		return
	}
	g.avatars.mu.Lock()
	defer g.avatars.mu.Unlock()
	if g.avatars.cache == nil {
		g.avatars.cache = map[string]avatarEntry{}
	}
	item, ok := g.avatars.cache[request.Chat]
	if !ok || time.Now().After(item.until) {
		ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
		defer cancel()
		item = fetchAvatar(ctx, sess.client, chat)
		// At most 32 entries / 4 MiB per source, including negative lookups.
		bytes := len(item.body)
		for key, v := range g.avatars.cache {
			if time.Now().After(v.until) {
				delete(g.avatars.cache, key)
			} else {
				bytes += len(v.body)
			}
		}
		for key, v := range g.avatars.cache {
			if len(g.avatars.cache) < 32 && bytes <= 4<<20 {
				break
			}
			delete(g.avatars.cache, key)
			bytes -= len(v.body)
		}
		g.avatars.cache[request.Chat] = item
	}
	if item.status != 200 {
		http.Error(w, "native_avatar_unavailable", item.status)
		return
	}
	w.Header().Set("Content-Type", item.mime)
	w.Header().Set("Content-Security-Policy", "sandbox; default-src 'none'")
	w.Write(item.body)
}
