package main

import (
	"context"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"testing"
)

type callerTestStore struct {
	store.NoopStore
	phone, lid types.JID
	info       types.ContactInfo
}

func (s *callerTestStore) GetPNForLID(context.Context, types.JID) (types.JID, error) {
	return s.phone, nil
}
func (s *callerTestStore) GetLIDForPN(context.Context, types.JID) (types.JID, error) {
	return s.lid, nil
}
func (s *callerTestStore) GetContact(context.Context, types.JID) (types.ContactInfo, error) {
	return s.info, nil
}

func TestCallerOnlyUsesReciprocalDeviceIdentity(t *testing.T) {
	lid := types.NewJID("111111111111111", types.HiddenUserServer)
	phone := types.NewJID("994100000777", types.DefaultUserServer)
	s := &callerTestStore{phone: phone, lid: lid, info: types.ContactInfo{FullName: " Synthetic\nCaller ", PushName: "other"}}
	got := resolveCaller(context.Background(), lid, s, s)
	if got == nil || got.Phone != phone.String() || got.Name != "SyntheticCaller" {
		t.Fatalf("confirmed caller missing: %#v", got)
	}
	s.lid = types.NewJID("222222222222222", types.HiddenUserServer)
	if resolveCaller(context.Background(), lid, s, s) != nil {
		t.Fatal("conflicting reverse identity accepted")
	}
	if resolveCaller(context.Background(), lid, nil, s) != nil {
		t.Fatal("contact name substituted for phone proof")
	}
	got = resolveCaller(context.Background(), phone, nil, s)
	if got == nil || got.Phone != phone.String() {
		t.Fatal("direct phone lost")
	}
	if resolveCaller(context.Background(), types.NewJID("123456", types.GroupServer), s, s) != nil {
		t.Fatal("group treated as caller")
	}
}

func TestOfferIdentityPrecedesContactSyncAndRejectsConflicts(t *testing.T) {
	peer := types.NewJID("111111111111111", types.HiddenUserServer)
	phone := types.NewJID("994100000777", types.DefaultUserServer)
	meta := types.BasicCallMeta{From: peer, CallCreator: peer, CallCreatorAlt: phone}
	local := &callerTestStore{info: types.ContactInfo{PushName: "Synthetic Caller"}}
	got := resolveOfferCaller(context.Background(), meta, local, local)
	if got == nil || got.Phone != phone.String() || got.Name != "Synthetic Caller" {
		t.Fatal("offer proof was delayed until contact sync")
	}
	if !local.phone.IsEmpty() || !local.lid.IsEmpty() {
		t.Fatal("offer mutated routing store")
	}
	for _, field := range []string{"creator", "group", "alternate", "forward", "reverse"} {
		bad := meta
		store := *local
		switch field {
		case "creator":
			bad.CallCreator = phone
		case "group":
			bad.GroupJID = types.NewJID("123456", types.GroupServer)
		case "alternate":
			bad.CallCreatorAlt = peer
		case "forward":
			store.phone = types.NewJID("994100000888", types.DefaultUserServer)
		case "reverse":
			store.lid = types.NewJID("222222222222222", types.HiddenUserServer)
		}
		if resolveOfferCaller(context.Background(), bad, &store, &store) != nil {
			t.Fatal("accepted conflicting offer", field)
		}
	}
}
