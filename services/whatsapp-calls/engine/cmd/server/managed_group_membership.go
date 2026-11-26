package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
	"wacalls/internal/unified"
)

func groupContainsAccount(info *types.GroupInfo, chat, account, ownLID types.JID) bool {
	if info == nil || info.JID != chat || info.Suspended || account.IsEmpty() ||
		info.ParticipantCount == 0 || info.ParticipantCount != len(info.Participants) {
		return false
	}
	for _, member := range info.Participants {
		if member.Error != 0 || member.AddRequest != nil {
			continue
		}
		jid, phone, lid := member.JID.ToNonAD(), member.PhoneNumber.ToNonAD(), member.LID.ToNonAD()
		if (jid.Server == types.DefaultUserServer && jid != account) ||
			(!phone.IsEmpty() && phone != account) ||
			(!ownLID.IsEmpty() && ((jid.Server == types.HiddenUserServer && jid != ownLID) ||
				(!lid.IsEmpty() && lid != ownLID))) {
			continue
		}
		if jid == account || phone == account || (!ownLID.IsEmpty() && (jid == ownLID || lid == ownLID)) {
			return true
		}
	}
	return false
}

type groupSessionIdentity struct {
	epoch   uint64
	account types.JID
	lid     types.JID
}

func freshGroupMembership(ctx context.Context, chat types.JID, boundAccount string, current func() groupSessionIdentity, fetch func(context.Context, types.JID) (*types.GroupInfo, error)) (string, error) {
	before := current()
	if before.account.IsEmpty() || before.account.String() != boundAccount {
		return "", unified.ErrGroupSession
	}
	ctx, cancel := context.WithTimeout(ctx, 6*time.Second)
	defer cancel()
	info, err := fetch(ctx, chat)
	if current() != before {
		return "", unified.ErrGroupSession
	}
	if errors.Is(err, whatsmeow.ErrNotInGroup) || errors.Is(err, whatsmeow.ErrGroupNotFound) {
		return "", unified.ErrGroupMembership
	}
	if err != nil || ctx.Err() != nil {
		return "", unified.ErrGroupCheckUnavailable
	}
	if !groupContainsAccount(info, chat, before.account, before.lid) {
		return "", unified.ErrGroupMembership
	}
	if before.lid.Server == types.HiddenUserServer && !before.lid.IsEmpty() {
		return before.lid.String(), nil
	}
	return "", nil
}

func verifyManagedGroupMembership(ctx context.Context, session *Session, target, boundAccount string) (string, error) {
	chat, err := types.ParseJID(target)
	if err != nil || !groupPeer.MatchString(target) {
		return "", unified.ErrGroupMembership
	}
	client := session.client
	if client == nil {
		return "", unified.ErrGroupSession
	}
	current := func() groupSessionIdentity {
		session.mu.Lock()
		defer session.mu.Unlock()
		if session.auth.State != "open" || session.client != client || !session.transportReady() {
			return groupSessionIdentity{}
		}
		return groupSessionIdentity{session.connectionEpoch, client.Store.GetJID().ToNonAD(), client.Store.GetLID().ToNonAD()}
	}
	// GetGroupInfo always sends a new WhatsApp group query. The cached roster
	// used by the group overview cannot participate in this decision.
	return freshGroupMembership(ctx, chat, boundAccount, current, client.GetGroupInfo)
}

func (s *server) handleManagedGroupMembership(w http.ResponseWriter, r *http.Request) {
	g := s.appGateway
	if g == nil || g.cfg.Messaging == nil {
		http.Error(w, "native_messaging_disabled", http.StatusConflict)
		return
	}
	var request struct {
		Scope unified.Scope `json:"scope"`
		Chat  string        `json:"chat"`
	}
	decoder := json.NewDecoder(io.LimitReader(r.Body, 4097))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&request) != nil || decoder.Decode(new(any)) != io.EOF ||
		request.Scope != g.cfg.Messaging.Scope || !groupPeer.MatchString(request.Chat) {
		http.Error(w, "native_group_invalid", http.StatusBadRequest)
		return
	}
	infos := s.sessions.infos()
	if len(infos) != 1 || infos[0].State != "open" {
		writeJSON(w, http.StatusConflict, map[string]string{"error": unified.ErrGroupSession.Error()})
		return
	}
	session, ok := s.sessions.Get(infos[0].ID)
	if !ok {
		writeJSON(w, http.StatusConflict, map[string]string{"error": unified.ErrGroupSession.Error()})
		return
	}
	lid, err := verifyManagedGroupMembership(r.Context(), session, request.Chat, g.cfg.Account)
	if err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"member": true, "scope": request.Scope, "chat": request.Chat, "account_lid": lid})
}
