package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"time"

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

func verifyManagedGroupMembership(ctx context.Context, session *Session, target, boundAccount string) (string, error) {
	chat, err := types.ParseJID(target)
	if err != nil || !groupPeer.MatchString(target) {
		return "", unified.ErrGroupMembership
	}
	client := session.client
	if !session.transportReady() || client.Store.GetJID().ToNonAD().String() != boundAccount {
		return "", unified.ErrGroupSession
	}
	ctx, cancel := context.WithTimeout(ctx, 6*time.Second)
	defer cancel()
	// The group overview uses a cached roster. Admission and dispatch need
	// fresh evidence from the session that will actually send.
	account, lid := client.Store.GetJID().ToNonAD(), client.Store.GetLID().ToNonAD()
	info, err := client.GetGroupInfo(ctx, chat)
	if session.client != client || !session.transportReady() ||
		client.Store.GetJID().ToNonAD() != account || account.String() != boundAccount {
		return "", unified.ErrGroupSession
	}
	if err != nil || !groupContainsAccount(info, chat, account, lid) {
		return "", unified.ErrGroupMembership
	}
	if lid.Server == types.HiddenUserServer && !lid.IsEmpty() {
		return lid.String(), nil
	}
	return "", nil
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
