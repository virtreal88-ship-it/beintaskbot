package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"go.mau.fi/whatsmeow/types"
	"wacalls/internal/unified"
)

type groupSnapshot struct {
	Status     string   `json:"status"`
	ObservedAt string   `json:"observedAt"`
	Name       string   `json:"group_name"`
	Phones     []string `json:"phones"`
	Total      int      `json:"total"`
	Unresolved int      `json:"unresolved"`
}
type groupCacheEntry struct {
	snapshot groupSnapshot
	until    time.Time
}
type managedGroups struct {
	mu    sync.Mutex
	cache map[string]groupCacheEntry
	queue chan types.JID
}

var groupPhone = regexp.MustCompile(`^[1-9][0-9]{7,14}$`)
var groupPeer = regexp.MustCompile(`^[0-9]{5,30}(?:-[0-9]{5,20})?@g\.us$`)

// Provider-supplied PN only. A numeric LID is never a telephone number.
func projectManagedGroup(chat types.JID, info *types.GroupInfo, now time.Time) (groupSnapshot, error) {
	if info == nil || info.JID != chat || !groupPeer.MatchString(chat.String()) || len(info.Participants) > 2048 || info.ParticipantCount > 2048 {
		return groupSnapshot{}, errors.New("group_unavailable")
	}
	name := strings.TrimSpace(info.Name)
	if !utf8.ValidString(name) || len(name) > 1024 || strings.ContainsAny(name, "\x00\r\n") {
		return groupSnapshot{}, errors.New("group_unavailable")
	}
	out := groupSnapshot{Status: "available", ObservedAt: now.UTC().Format(time.RFC3339Nano), Name: name, Phones: []string{}, Total: len(info.Participants)}
	if info.ParticipantCount > out.Total {
		out.Total = info.ParticipantCount
	}
	phones := map[string]bool{}
	for _, p := range info.Participants {
		pn := p.PhoneNumber.ToNonAD()
		if pn.IsEmpty() {
			pn = p.JID.ToNonAD()
		}
		if p.Error != 0 || pn.Server != types.DefaultUserServer || !groupPhone.MatchString(pn.User) {
			continue
		}
		phones["+"+pn.User] = true
	}
	for phone := range phones {
		out.Phones = append(out.Phones, phone)
	}
	sort.Strings(out.Phones)
	out.Unresolved = out.Total - len(out.Phones)
	return out, nil
}

// One bounded worker and one shared cache per exact source. Metadata I/O is
// outside the message ACK/call/audio paths; failures never clear known names.
func (g *appGateway) refreshGroups(ctx context.Context) {
	if g == nil || g.groups == nil {
		return
	}
	for {
		select {
		case <-ctx.Done():
			return
		case chat := <-g.groups.queue:
			_, _ = g.groupSnapshot(ctx, chat)
		}
	}
}
func (g *appGateway) groupSnapshot(ctx context.Context, chat types.JID) (groupSnapshot, error) {
	if g == nil || g.groups == nil || g.messages == nil || !groupPeer.MatchString(chat.String()) {
		return groupSnapshot{}, errors.New("group_unavailable")
	}
	g.groups.mu.Lock()
	defer g.groups.mu.Unlock()
	now := time.Now()
	if item, ok := g.groups.cache[chat.String()]; ok && now.Before(item.until) {
		if item.snapshot.Status != "available" {
			return groupSnapshot{}, errors.New("group_unavailable")
		}
		return item.snapshot, nil
	}
	if len(g.groups.cache) >= 512 {
		for key, item := range g.groups.cache {
			if !now.Before(item.until) {
				delete(g.groups.cache, key)
			}
		}
		if len(g.groups.cache) >= 512 {
			return groupSnapshot{}, errors.New("group_busy")
		}
	}
	// A failed lookup also has a cooldown; repeated messages cannot flood WA.
	g.groups.cache[chat.String()] = groupCacheEntry{until: now.Add(30 * time.Second)}
	infos := g.server.sessions.infos()
	if len(infos) != 1 || infos[0].State != "open" {
		return groupSnapshot{}, errors.New("source_unavailable")
	}
	s, ok := g.server.sessions.Get(infos[0].ID)
	if !ok || !s.transportReady() || s.client.Store.ID == nil || s.client.Store.ID.ToNonAD().String() != g.cfg.Account {
		return groupSnapshot{}, errors.New("source_identity_mismatch")
	}
	ctx, cancel := context.WithTimeout(ctx, 6*time.Second)
	defer cancel()
	info, err := s.client.GetGroupInfo(ctx, chat)
	if err != nil {
		return groupSnapshot{}, errors.New("group_unavailable")
	}
	snapshot, err := projectManagedGroup(chat, info, time.Now())
	if err != nil {
		return groupSnapshot{}, err
	}
	body, err := json.Marshal(map[string]any{"kind": "group_metadata", "chat": chat.String(), "snapshot": snapshot})
	if err != nil {
		return groupSnapshot{}, err
	}
	_, err = g.messages.Append(ctx, "group:"+chat.String()+":"+snapshot.ObservedAt, body)
	if err != nil {
		return groupSnapshot{}, err
	}
	g.groups.cache[chat.String()] = groupCacheEntry{snapshot: snapshot, until: time.Now().Add(time.Minute)}
	return snapshot, nil
}

// Private supervisor route: source scope and account are both rechecked.
func (s *server) handleManagedGroup(w http.ResponseWriter, r *http.Request) {
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
	if d.Decode(&request) != nil || d.Decode(new(any)) != io.EOF || request.Scope != g.cfg.Messaging.Scope || !groupPeer.MatchString(request.Chat) {
		http.Error(w, "native_group_invalid", 400)
		return
	}
	chat, err := types.ParseJID(request.Chat)
	if err != nil {
		http.Error(w, "native_group_invalid", 400)
		return
	}
	value, err := g.groupSnapshot(r.Context(), chat)
	if err != nil {
		http.Error(w, "native_group_unavailable", 503)
		return
	}
	writeJSON(w, 200, value)
}
