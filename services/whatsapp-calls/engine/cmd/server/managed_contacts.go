package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"
	"unicode"

	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"wacalls/internal/unified"
)

type callerIdentity struct {
	Phone string `json:"phone_jid"`
	Name  string `json:"display_name"`
}

// A direct offer carries a protocol-confirmed PN alongside its LID, before
// contact sync necessarily populates the local mapping. Never infer it from
// a name, group participant, or a mismatching creator; retain it per call only.
func resolveOfferCaller(ctx context.Context, meta types.BasicCallMeta, lids store.LIDStore, contacts store.ContactStore) *callerIdentity {
	peer, creator, alternate := meta.From.ToNonAD(), meta.CallCreator.ToNonAD(), meta.CallCreatorAlt.ToNonAD()
	if !meta.GroupJID.IsEmpty() || peer != creator || !directPeer(peer.String()) {
		return nil
	}
	if peer.Server == types.DefaultUserServer {
		return resolveCaller(ctx, peer, lids, contacts)
	}
	if alternate.Server != types.DefaultUserServer || !directPeer(alternate.String()) {
		return nil
	}
	if lids != nil {
		pn, err := lids.GetPNForLID(ctx, peer)
		if err != nil || !pn.IsEmpty() && pn.ToNonAD() != alternate {
			return nil
		}
		lid, err := lids.GetLIDForPN(ctx, alternate)
		if err != nil || !lid.IsEmpty() && lid.ToNonAD() != peer {
			return nil
		}
	}
	result := resolveCaller(ctx, alternate, nil, contacts)
	return result
}

// Only the paired device's protocol mapping proves LID -> PN. Contact labels
// never establish identity. Both lookups use local stores, not provider I/O.
func resolveCaller(ctx context.Context, peer types.JID, lids store.LIDStore, contacts store.ContactStore) *callerIdentity {
	if !directPeer(peer.String()) {
		return nil
	}
	phone := peer
	if peer.Server == types.HiddenUserServer {
		if lids == nil {
			return nil
		}
		var err error
		phone, err = lids.GetPNForLID(ctx, peer)
		if err != nil || phone.Server != types.DefaultUserServer || !directPeer(phone.String()) {
			return nil
		}
		reverse, err := lids.GetLIDForPN(ctx, phone)
		if err != nil || reverse != peer {
			return nil
		}
	}
	if phone.Server != types.DefaultUserServer {
		return nil
	}
	result := &callerIdentity{Phone: phone.String()}
	if contacts != nil {
		for _, jid := range []types.JID{phone, peer} {
			info, err := contacts.GetContact(ctx, jid)
			if err != nil {
				continue
			}
			for _, name := range []string{info.FullName, info.PushName, info.BusinessName, info.FirstName} {
				name = strings.TrimSpace(strings.Map(func(r rune) rune {
					if unicode.IsControl(r) {
						return -1
					}
					return r
				}, name))
				if name != "" {
					runes := []rune(name)
					if len(runes) > 256 {
						runes = runes[:256]
					}
					result.Name = string(runes)
					return result
				}
			}
		}
	}
	return result
}

func (g *appGateway) caller(ctx context.Context, sess *Session, peer string) *callerIdentity {
	if !g.cfg.Managed || sess == nil || sess.client == nil || sess.client.Store == nil || sess.client.Store.ID == nil || sess.client.Store.ID.ToNonAD().String() != g.cfg.Account {
		return nil
	}
	jid, err := types.ParseJID(peer)
	if err != nil {
		return nil
	}
	return resolveCaller(ctx, jid, sess.client.Store.LIDs, sess.client.Store.Contacts)
}

func (s *server) handleManagedContact(w http.ResponseWriter, r *http.Request) {
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
	if d.Decode(&request) != nil || d.Decode(new(any)) != io.EOF || request.Scope != g.cfg.Messaging.Scope || !directPeer(request.Chat) {
		http.Error(w, "native_contact_invalid", 400)
		return
	}
	infos := s.sessions.infos()
	if len(infos) != 1 {
		http.Error(w, "source_unavailable", 503)
		return
	}
	sess, ok := s.sessions.Get(infos[0].ID)
	if !ok {
		http.Error(w, "source_unavailable", 503)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), time.Second)
	defer cancel()
	result := g.caller(ctx, sess, request.Chat)
	if result == nil {
		http.Error(w, "caller_unconfirmed", 404)
		return
	}
	writeJSON(w, 200, map[string]any{"scope": request.Scope, "peer_jid": request.Chat, "caller": result})
}
