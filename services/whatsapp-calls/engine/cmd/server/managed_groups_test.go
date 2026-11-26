package main

import (
	"context"
	"errors"
	"go.mau.fi/whatsmeow/types"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
	"wacalls/internal/unified"
)

func TestManagedGroupUsesOnlyExactProviderGroupAndConfirmedPhones(t *testing.T) {
	chat := types.NewJID("120363123456789", types.GroupServer)
	pn := types.NewJID("15550000002", types.DefaultUserServer)
	lid := types.NewJID("1234567890", types.HiddenUserServer)
	info := &types.GroupInfo{JID: chat, GroupName: types.GroupName{Name: "Test group"}, Participants: []types.GroupParticipant{{JID: lid, PhoneNumber: pn}, {JID: types.NewJID("987654321", types.HiddenUserServer)}}}
	out, err := projectManagedGroup(chat, info, time.Now())
	if err != nil || out.Name != "Test group" || out.Total != 2 || out.Unresolved != 1 || len(out.Phones) != 1 || out.Phones[0] != "+15550000002" {
		t.Fatal("provider roster lost or LID became a phone", err)
	}
	wrong := types.NewJID("120363999999999", types.GroupServer)
	if _, err = projectManagedGroup(wrong, info, time.Now()); err == nil {
		t.Fatal("same name authorized another group")
	}
	info.ParticipantCount = 2049
	if _, err = projectManagedGroup(chat, info, time.Now()); err == nil {
		t.Fatal("unbounded roster")
	}
}

func TestManagedGroupRejectsOtherScopeBeforeAnyProviderRead(t *testing.T) {
	s := &server{}
	w := httptest.NewRecorder()
	s.handleManagedGroup(w, httptest.NewRequest("POST", "/internal/messages/group", strings.NewReader(`{}`)))
	if w.Code != 409 {
		t.Fatal(w.Code)
	}
	s.appGateway = &appGateway{cfg: appGatewayConfig{Messaging: &managedMessagingConfig{Scope: unified.Scope{Tenant: "expected"}}}}
	w = httptest.NewRecorder()
	s.handleManagedGroup(w, httptest.NewRequest("POST", "/internal/messages/group", strings.NewReader(`{"scope":{"tenant":"other"},"chat":"120363123456789@g.us"}`)))
	if w.Code != 400 {
		t.Fatal(w.Code)
	}
}

func TestGroupMembershipRequiresExactAccountInCompleteProviderRoster(t *testing.T) {
	chat := types.NewJID("120363123456789", types.GroupServer)
	phone := types.NewJID("15550000002", types.DefaultUserServer)
	lid := types.NewJID("1234567890", types.HiddenUserServer)
	other := types.NewJID("15550000003", types.DefaultUserServer)
	entry := types.GroupParticipant{JID: lid}
	info := &types.GroupInfo{JID: chat, ParticipantCount: 1, Participants: []types.GroupParticipant{entry}}
	if !groupContainsAccount(info, chat, phone, lid) {
		t.Fatal("the linked device's own LID is an exact match")
	}
	if groupContainsAccount(info, chat, phone, types.EmptyJID) {
		t.Fatal("a LID without the linked device's identity proves nothing")
	}
	info.Participants[0].PhoneNumber = other
	if groupContainsAccount(info, chat, phone, lid) {
		t.Fatal("conflicting phone identity was accepted")
	}
	info.Participants[0] = types.GroupParticipant{JID: phone}
	if !groupContainsAccount(info, chat, phone, lid) {
		t.Fatal("the exact bound phone must be accepted")
	}
	info.ParticipantCount = 2
	if groupContainsAccount(info, chat, phone, lid) {
		t.Fatal("an incomplete roster was accepted")
	}
	info.ParticipantCount = 0
	if groupContainsAccount(info, chat, phone, lid) {
		t.Fatal("a roster with unconfirmed completeness was accepted")
	}
	info.ParticipantCount = 1
	if groupContainsAccount(info, types.NewJID("120363123456788", types.GroupServer), phone, lid) {
		t.Fatal("another group was accepted")
	}
}

func TestFreshGroupMembershipIgnoresCachedRosterAndBindsTheConnection(t *testing.T) {
	chat := types.NewJID("120363123456789", types.GroupServer)
	account := types.NewJID("15550000002", types.DefaultUserServer)
	previous := &types.GroupInfo{JID: chat, ParticipantCount: 1, Participants: []types.GroupParticipant{{JID: account}}}
	withoutAccount := &types.GroupInfo{JID: chat, ParticipantCount: 1, Participants: []types.GroupParticipant{{JID: types.NewJID("15550000003", types.DefaultUserServer)}}}
	ctx := context.Background()
	roster, err := projectManagedGroup(chat, previous, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	gateway := &appGateway{messages: new(unified.Journal), groups: &managedGroups{
		cache: map[string]groupCacheEntry{chat.String(): {snapshot: roster, until: time.Now().Add(time.Minute)}},
	}}
	for _, test := range []struct {
		name              string
		info              *types.GroupInfo
		lookupErr         error
		reconnectedBefore bool
		reconnectedDuring bool
		want              error
	}{
		{name: "removed from group, overview cache still positive", info: withoutAccount, want: unified.ErrGroupMembership},
		{name: "reconnected with an old positive overview", info: withoutAccount, reconnectedBefore: true, want: unified.ErrGroupMembership},
		{name: "reconnected during a positive provider lookup", info: previous, reconnectedDuring: true, want: unified.ErrGroupSession},
		{name: "temporary check outage with a positive overview", lookupErr: context.DeadlineExceeded, want: unified.ErrGroupCheckUnavailable},
		{name: "fresh confirmation after reconnect", info: previous, reconnectedBefore: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			visible, err := gateway.groupSnapshot(ctx, chat)
			if err != nil || len(visible.Phones) != 1 || visible.Phones[0] != "+"+account.User {
				t.Fatalf("the positive overview cache should still be present: %v", err)
			}
			connection := groupSessionIdentity{epoch: 1, account: account}
			if test.reconnectedBefore {
				connection.epoch++
			}
			calls := 0
			_, err = freshGroupMembership(ctx, chat, account.String(), func() groupSessionIdentity { return connection },
				func(_ context.Context, requested types.JID) (*types.GroupInfo, error) {
					calls++
					if requested != chat {
						t.Fatalf("unexpected group: %s", requested)
					}
					if test.reconnectedDuring {
						connection.epoch++
					}
					return test.info, test.lookupErr
				})
			if calls != 1 || !errors.Is(err, test.want) {
				t.Fatalf("expected one fresh query and error %v, got %d queries and %v", test.want, calls, err)
			}
		})
	}
}
