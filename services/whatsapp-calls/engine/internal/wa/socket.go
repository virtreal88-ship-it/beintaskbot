package wa

import (
	"context"
	"errors"
	"time"

	"wacalls/internal/voip/core"
	"wacalls/internal/voip/signaling"

	"go.mau.fi/whatsmeow"
	waBinary "go.mau.fi/whatsmeow/binary"
	"go.mau.fi/whatsmeow/types"
)

type Socket struct {
	cli *whatsmeow.Client
}

func NewSocket(cli *whatsmeow.Client) *Socket { return &Socket{cli: cli} }

var _ core.VoipSocket = (*Socket)(nil)

func (s *Socket) di() *whatsmeow.DangerousInternalClient { return s.cli.DangerousInternals() }

func (s *Socket) OwnPN() types.JID { return s.di().GetOwnID() }

func (s *Socket) OwnLID() types.JID { return s.di().GetOwnLID() }

func (s *Socket) AccountDeviceIdentityNode() (waBinary.Node, bool) {
	if s.cli.Store == nil || s.cli.Store.Account == nil {
		return waBinary.Node{}, false
	}
	return s.di().MakeDeviceIdentityNode(), true
}

func (s *Socket) SendNode(ctx context.Context, node waBinary.Node) error {
	return s.di().SendNode(ctx, node)
}

// Preserve the offered routing and creator separately. Client.RejectCall drops
// the device address, substitutes creator for route and only awaits a write.
// A correlated server ACK is required here; it still is not a peer UI receipt.
func (s *Socket) RejectIncomingCall(ctx context.Context, peer, creator types.JID, callID string) error {
	own := s.OwnPN()
	if own.IsEmpty() {
		return errors.New("transport_unconfirmed")
	}
	node := buildRejectRequest(own, peer, creator, callID, s.cli.GenerateMessageID())
	return rejectWithAcknowledgement(ctx, s.Query, node)
}

func buildRejectRequest(own, peer, creator types.JID, callID, requestID string) waBinary.Node {
	// Match upstream 592721a (fixes whatsmeow #1182 for our exact SDK pin):
	// sender + count=0, with NO tctoken child. Retain the offered destination
	// and creator separately, plus our bounded correlated-ACK requirement.
	node := signaling.BuildRejectStanza(peer, callID, creator)
	node.Attrs["from"] = own.ToNonAD()
	node.Attrs["id"] = requestID
	children := node.GetChildren()
	children[0].Attrs["count"] = "0"
	node.Content = children
	return node
}

func rejectWithAcknowledgement(ctx context.Context, query func(context.Context, waBinary.Node) (*waBinary.Node, error), node waBinary.Node) error {
	response, err := query(ctx, node)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return errors.New("ack_timeout")
		}
		return errors.New("transport_unconfirmed")
	}
	if response == nil {
		return errors.New("ack_timeout")
	}
	if response.Tag != "ack" || response.Attrs["id"] != node.Attrs["id"] || response.Attrs["class"] != "call" || response.Attrs["type"] != "reject" {
		return errors.New("ack_invalid")
	}
	if value, exists := response.Attrs["error"]; exists && value != "0" && value != 0 {
		return errors.New("ack_rejected")
	}
	for _, child := range response.GetChildren() {
		if child.Tag == "error" {
			return errors.New("ack_rejected")
		}
	}
	return nil
}

func (s *Socket) Query(ctx context.Context, node waBinary.Node) (*waBinary.Node, error) {
	id, _ := node.Attrs["id"].(string)
	if id == "" {
		return nil, s.di().SendNode(ctx, node)
	}
	di := s.di()
	ch := di.WaitResponse(id)
	if err := di.SendNode(ctx, node); err != nil {
		di.CancelResponse(id, ch)
		return nil, err
	}
	select {
	case resp := <-ch:
		return resp, nil
	case <-time.After(15 * time.Second):
		di.CancelResponse(id, ch)
		return nil, nil
	case <-ctx.Done():
		di.CancelResponse(id, ch)
		return nil, ctx.Err()
	}
}

func (s *Socket) GetUSyncDevices(ctx context.Context, jids []types.JID) ([]types.JID, error) {
	return s.cli.GetUserDevices(ctx, jids)
}

func (s *Socket) AssertSessions(ctx context.Context, jids []types.JID, force bool) error {
	return nil
}

func (s *Socket) CreateParticipantNodes(ctx context.Context, devices []types.JID, callKey []byte, encAttrs waBinary.Attrs) ([]waBinary.Node, bool, error) {
	plaintext, err := signaling.EncodeCallKeyMessage(callKey)
	if err != nil {
		return nil, false, err
	}
	id := s.cli.GenerateMessageID()
	return s.di().EncryptMessageForDevices(ctx, devices, id, plaintext, plaintext, encAttrs)
}

func (s *Socket) DecryptCallKey(ctx context.Context, from types.JID, encChild *waBinary.Node) ([]byte, error) {
	typ, _ := encChild.Attrs["type"].(string)
	isPreKey := typ == "pkmsg"
	plaintext, _, err := s.di().DecryptDM(ctx, encChild, from, isPreKey, time.Now())
	if err != nil {
		return nil, err
	}
	return signaling.DecodeCallKeyPlaintext(plaintext)
}

func (s *Socket) GetTCToken(ctx context.Context, jid types.JID) ([]byte, error) {
	if s.cli.Store == nil || s.cli.Store.PrivacyTokens == nil {
		return nil, nil
	}
	for _, cand := range []types.JID{s.ResolveLIDForPN(ctx, jid).ToNonAD(), jid.ToNonAD()} {
		if cand.IsEmpty() {
			continue
		}
		tok, err := s.cli.Store.PrivacyTokens.GetPrivacyToken(ctx, cand)
		if err != nil {
			return nil, err
		}
		if tok != nil && len(tok.Token) > 0 {
			return tok.Token, nil
		}
	}
	return nil, nil
}

func (s *Socket) ResolveLIDForPN(ctx context.Context, pn types.JID) types.JID {
	if pn.Server == types.HiddenUserServer {
		return pn
	}
	if s.cli.Store != nil && s.cli.Store.LIDs != nil {
		if lid, err := s.cli.Store.LIDs.GetLIDForPN(ctx, pn); err == nil && !lid.IsEmpty() {
			return lid
		}
	}
	if info, err := s.cli.GetUserInfo(ctx, []types.JID{pn}); err == nil {
		if lid := info[pn].LID; !lid.IsEmpty() {
			return lid
		}
	}
	return pn
}
