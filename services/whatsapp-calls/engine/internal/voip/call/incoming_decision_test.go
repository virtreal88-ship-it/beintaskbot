package call

import (
	"context"
	"testing"

	waBinary "go.mau.fi/whatsmeow/binary"
	"go.mau.fi/whatsmeow/types"
	"wacalls/internal/voip/core"
)

type incomingDecisionSocket struct {
	core.VoipSocket
	sent []string
}

func (s *incomingDecisionSocket) OwnLID() types.JID { return types.NewJID("200", "lid") }
func (s *incomingDecisionSocket) OwnPN() types.JID  { return types.NewJID("200", "s.whatsapp.net") }
func (s *incomingDecisionSocket) SendNode(_ context.Context, n waBinary.Node) error {
	for _, c := range n.GetChildren() {
		s.sent = append(s.sent, c.Tag)
	}
	return nil
}
func (s *incomingDecisionSocket) RejectIncomingCall(_ context.Context, _, _ types.JID, _ string) error {
	s.sent = append(s.sent, "reject")
	return nil
}

func TestIncomingOfferDoesNotPreacceptBeforeOperatorDecision(t *testing.T) {
	for _, decision := range []string{"accept", "reject"} {
		t.Run(decision, func(t *testing.T) {
			s := &incomingDecisionSocket{}
			m := NewCallManager(s, nil)
			peer := types.NewJID("100", "lid")
			m.HandleCallOffer(context.Background(), &waBinary.Node{Tag: "call", Content: []waBinary.Node{{Tag: "offer", Attrs: waBinary.Attrs{"call-id": "test-call", "call-creator": peer}}}}, peer)
			if len(s.sent) != 0 || !m.CurrentCall().CanAccept() {
				t.Fatal("incoming ringing advanced signalling without operator decision")
			}
			if decision == "reject" {
				if e := m.RejectCall(context.Background(), "test-call", core.EndCallReasonDeclined); e != nil {
					t.Fatal(e)
				}
				if len(s.sent) != 1 || s.sent[0] != "reject" {
					t.Fatal("reject was preceded by preaccept")
				}
			} else {
				if e := m.AcceptCall(context.Background(), "test-call"); e != nil {
					t.Fatal(e)
				}
				if len(s.sent) != 1 || s.sent[0] != "preaccept" {
					t.Fatal("accept omitted preaccept")
				}
				if e := m.AcceptCall(context.Background(), "test-call"); e == nil || len(s.sent) != 1 {
					t.Fatal("accept repeated")
				}
				m.cleanupMedia()
			}
		})
	}
}
