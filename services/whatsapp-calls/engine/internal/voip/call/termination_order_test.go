package call

import (
	"context"
	waBinary "go.mau.fi/whatsmeow/binary"
	"go.mau.fi/whatsmeow/types"
	"log/slog"
	"testing"
	"wacalls/internal/voip/core"
	"wacalls/internal/voip/media"
)

// No WhatsApp client or network. Only the terminal signaling method is needed.
type terminalSocket struct{ core.VoipSocket }

func (terminalSocket) Query(context.Context, waBinary.Node) (*waBinary.Node, error) { return nil, nil }
func (terminalSocket) RejectIncomingCall(context.Context, types.JID, types.JID, string) error {
	return nil
}

func TestEndedCallbackFollowsLocalMediaCleanup(t *testing.T) {
	for _, mode := range []string{"local", "reject", "remote"} {
		t.Run(mode, func(t *testing.T) {
			m := NewCallManager(terminalSocket{}, slog.Default())
			m.currentCall = NewIncomingCall("test-call", "12345@lid", "12345@lid", "", core.CallMediaTypeAudio)
			stop := make(chan struct{})
			m.keepaliveStop = stop
			m.rtpSession = media.NewWhatsAppOpusSession(123)
			callbacks := 0
			m.OnEnded = func(c *CallInfo) {
				callbacks++
				if !c.IsEnded() || m.rtpSession != nil || m.keepaliveStop != nil {
					t.Error("ended before media cleanup")
				}
				select {
				case <-stop:
				default:
					t.Error("keepalive still running")
				}
			}
			switch mode {
			case "local":
				if e := m.EndCall(context.Background(), core.EndCallReasonUserEnded); e != nil {
					t.Fatal(e)
				}
			case "reject":
				if e := m.RejectCall(context.Background(), "test-call", core.EndCallReasonDeclined); e != nil {
					t.Fatal(e)
				}
			case "remote":
				m.HandleCallTerminate(&waBinary.Node{Tag: "call", Content: []waBinary.Node{{Tag: "terminate", Attrs: waBinary.Attrs{"call-id": "test-call"}}}})
			}
			if callbacks != 1 {
				t.Fatal("missing terminal confirmation")
			}
		})
	}
}
