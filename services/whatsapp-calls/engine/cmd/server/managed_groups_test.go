package main

import (
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
