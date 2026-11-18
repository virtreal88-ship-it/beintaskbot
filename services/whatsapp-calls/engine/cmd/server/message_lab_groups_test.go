//go:build unifiedlab

package main

import (
	"go.mau.fi/whatsmeow/types"
	"testing"
)

func TestGroupDiscoveryRequiresExactTwoParticipantsNotJustName(t *testing.T) {
	m, _, _ := labFixture(t)
	self, _ := types.ParseJID(m.cfg.Scope.Account)
	peer, _ := types.ParseJID(m.cfg.AllowedChats[0])
	jid, _ := types.ParseJID("120363000000000000@g.us")
	base := types.GroupInfo{JID: jid, GroupName: types.GroupName{Name: "Beintaskbot TEST"}, Participants: []types.GroupParticipant{{JID: self}, {JID: peer}}, ParticipantCount: 2}
	if !m.testGroupCandidate(&base) {
		t.Fatal("exact test group denied")
	}
	for _, change := range []func(*types.GroupInfo){
		func(g *types.GroupInfo) { g.Name = "Client data" },
		func(g *types.GroupInfo) { g.Participants[1].JID = self },
		func(g *types.GroupInfo) { g.Participants[1].JID = types.NewJID("15550000009", types.DefaultUserServer) },
		func(g *types.GroupInfo) { g.Participants[1].JID = types.NewJID("123456", types.HiddenUserServer) },
		func(g *types.GroupInfo) { g.ParticipantCount = 3 },
		func(g *types.GroupInfo) { g.IsParent = true },
		func(g *types.GroupInfo) { g.IsEphemeral = true },
		func(g *types.GroupInfo) { g.Participants[1].Error = 403 },
	} {
		bad := base
		bad.Participants = append([]types.GroupParticipant(nil), base.Participants...)
		change(&bad)
		if m.testGroupCandidate(&bad) {
			t.Fatal("unconfirmed membership admitted")
		}
	}
	good := base
	good.Participants = append([]types.GroupParticipant(nil), base.Participants...)
	good.Participants[1].PhoneNumber = peer
	good.Participants[1].JID = types.NewJID("123456", types.HiddenUserServer)
	if !m.testGroupCandidate(&good) {
		t.Fatal("provider-confirmed PN/LID denied")
	}
}
