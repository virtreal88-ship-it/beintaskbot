package wa

import (
	"context"
	"errors"
	"testing"

	waBinary "go.mau.fi/whatsmeow/binary"
	"go.mau.fi/whatsmeow/types"
	"wacalls/internal/voip/signaling"
)

func TestRejectRequiresExactServerAcknowledgement(t *testing.T) {
	node := signaling.BuildRejectStanza(types.NewJID("100", "lid"), "call", types.NewJID("101", "lid"))
	valid := func() *waBinary.Node {
		return &waBinary.Node{Tag: "ack", Attrs: waBinary.Attrs{"id": node.Attrs["id"], "class": "call", "type": "reject"}}
	}
	for _, tc := range []struct {
		name   string
		mutate func(*waBinary.Node)
		want   string
	}{
		{"accepted", func(*waBinary.Node) {}, ""},
		{"late other stanza", func(n *waBinary.Node) { n.Attrs["id"] = "previous" }, "ack_invalid"},
		{"different action", func(n *waBinary.Node) { n.Attrs["type"] = "preaccept" }, "ack_invalid"},
		{"different class", func(n *waBinary.Node) { n.Attrs["class"] = "message" }, "ack_invalid"},
		{"unknown frame", func(n *waBinary.Node) { n.Tag = "error" }, "ack_invalid"},
		{"server refusal", func(n *waBinary.Node) { n.Attrs["error"] = "403" }, "ack_rejected"},
		{"private error", func(n *waBinary.Node) { n.Content = []waBinary.Node{{Tag: "error", Content: []byte("SECRET")}} }, "ack_rejected"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			response := valid()
			tc.mutate(response)
			err := rejectWithAcknowledgement(context.Background(), func(context.Context, waBinary.Node) (*waBinary.Node, error) { return response, nil }, node)
			got := ""
			if err != nil {
				got = err.Error()
			}
			if got != tc.want {
				t.Fatalf("got %q want %q", got, tc.want)
			}
		})
	}
	for _, cause := range []error{nil, context.DeadlineExceeded, errors.New("PRIVATE transport body")} {
		err := rejectWithAcknowledgement(context.Background(), func(context.Context, waBinary.Node) (*waBinary.Node, error) { return nil, cause }, node)
		want := "ack_timeout"
		if cause != nil && !errors.Is(cause, context.DeadlineExceeded) {
			want = "transport_unconfirmed"
		}
		if err == nil || err.Error() != want {
			t.Fatal("missing/error reply was not fail closed")
		}
	}
}

func TestMinimalRejectPreservesOfferedRouteAndCreator(t *testing.T) {
	peer := types.JID{User: "100", Server: types.HiddenUserServer, Device: 4}
	creator := peer.ToNonAD()
	n := signaling.BuildRejectStanza(peer, "call", creator)
	children := n.GetChildren()
	if n.Tag != "call" || n.Attrs["to"] != peer || n.Attrs["id"] == "" || len(children) != 1 || children[0].Tag != "reject" || len(children[0].Attrs) != 2 || children[0].Attrs["call-id"] != "call" || children[0].Attrs["call-creator"] != creator || children[0].Content != nil {
		t.Fatal("reject lost routing or added unverified attributes/children")
	}
}

func TestRejectRequestMatchesUpstreamFixWithoutPrivacyToken(t *testing.T) {
	own := types.JID{User: "102", Server: types.DefaultUserServer, Device: 26}
	for _, server := range []string{types.DefaultUserServer, types.HiddenUserServer} {
		peer := types.JID{User: "100", Server: server, Device: 4}
		creator := types.JID{User: "101", Server: server}
		n := buildRejectRequest(own, peer, creator, "call", "request")
		children := n.GetChildren()
		if n.Tag != "call" || len(n.Attrs) != 3 || n.Attrs["from"] != own.ToNonAD() || n.Attrs["to"] != peer || n.Attrs["id"] != "request" || len(children) != 1 {
			t.Fatal("reject request lost sender or exact offered route")
		}
		reject := children[0]
		if reject.Tag != "reject" || len(reject.Attrs) != 3 || reject.Attrs["call-id"] != "call" || reject.Attrs["call-creator"] != creator || reject.Attrs["count"] != "0" || reject.Content != nil {
			t.Fatal("reject reintroduced token or changed upstream-compatible shape")
		}
	}
}
