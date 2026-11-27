package main

import (
	"context"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"wacalls/internal/unified"
	"wacalls/internal/voip/call"
	"wacalls/internal/voip/core"
	"wacalls/internal/voip/signaling"
	"wacalls/internal/voip/wanode"
	"wacalls/internal/wa"

	"go.mau.fi/whatsmeow"
	waBinary "go.mau.fi/whatsmeow/binary"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

type Session struct {
	id   string
	name string
	mgr  *SessionManager
	log  *slog.Logger

	client *whatsmeow.Client
	reg    *callRegistry
	diag   callDiagnostics

	mu              sync.Mutex
	auth            AuthSnapshot
	connectionEpoch uint64
}

func newSession(mgr *SessionManager, id, name string, client *whatsmeow.Client) *Session {
	client.SetMediaHTTPClient(unified.NewMediaHTTPClient())
	s := &Session{
		id:     id,
		name:   name,
		mgr:    mgr,
		log:    mgr.log.With("session", id),
		client: client,
		auth:   AuthSnapshot{State: "connecting"},
		reg:    newCallRegistry(),
	}
	if !mgr.nativeGateway.attachMessages(s) && (mgr.messageLab == nil || !mgr.messageLab.attach(s)) {
		client.AddEventHandler(s.handleEvent)
	}
	return s
}

// Managed browser conversations and stored recordings have separate bounds.
const managedCallMaxSeconds = 60 * 60

func liveCallMaxSeconds(store *recordingStore) int {
	if store != nil && store.cfg.Managed {
		return managedCallMaxSeconds
	}
	return store.maxSeconds()
}

func (s *Session) createCall(callID string) *call.CallManager {
	return s.createCallWithCaller(callID, nil)
}

func (s *Session) createCallWithCaller(callID string, caller *callerIdentity) *call.CallManager {
	cm := call.NewCallManager(wa.NewSocket(s.client), s.log)
	s.wireCall(cm, callID, caller)
	ac := &activeCall{cm: cm, id: callID}
	if !s.reg.addBounded(callID, ac, s.mgr.maxCalls) {
		return nil
	}
	seconds := liveCallMaxSeconds(s.mgr.recordings)
	ac.timer.Store(time.AfterFunc(time.Duration(seconds)*time.Second, func() {
		if current, ok := s.reg.get(callID); ok && current == ac {
			ac.markEnd("duration_limit")
			ac.interrupted.Store(true)
			s.terminateCall(callID, core.EndCallReasonUserEnded)
		}
	}))
	return cm
}

func (s *Session) wireCall(cm *call.CallManager, callID string, caller *callerIdentity) {
	cm.OnIncoming = func(c *call.CallInfo) {
		if ac, ok := s.reg.get(callID); !ok || ac.cm != cm {
			return
		}
		s.mgr.broker.upsertCall(CallRecord{
			Caller:    caller,
			SessionID: s.id, CallID: c.CallID, Direction: "inbound", Peer: c.PeerJid,
			StartedAt: time.Now().UnixMilli(), Status: StatusRinging,
		})
		s.mgr.broker.emitIncoming(s.id, c.CallID, c.PeerJid)
	}
	cm.OnStateChange = func(c *call.CallInfo) {
		if ac, ok := s.reg.get(callID); !ok || ac.cm != cm {
			return
		}
		if c.IsEnded() {
			// OnEnded is emitted after transport cleanup. A state transition alone
			// must not release the registry/recording while media still runs.
			return
		}
		dir := "outbound"
		if c.Direction == core.CallDirectionIncoming {
			dir = "inbound"
		}
		existing, _ := s.mgr.broker.getCall(c.CallID)
		rec := CallRecord{
			SessionID: s.id, CallID: c.CallID, Direction: dir, Peer: c.PeerJid,
			StartedAt: time.Now().UnixMilli(), Status: mapStatus(c.StateData.State),
			RemoteAnswered: dir == "outbound" && c.StateData.AcceptedAt != nil,
		}
		if existing != nil {
			rec.Recording = existing.Recording
			rec.Owner = existing.Owner
			rec.StartedAt = existing.StartedAt
			// Gateway start verifies the PN -> LID mapping before dialing. Keep
			// the exact approved route throughout signaling and recording.
			if existing.Owner != nil && existing.Direction == "outbound" {
				rec.Peer = existing.Peer
			}
		}
		s.mgr.broker.upsertCall(rec)
	}
	cm.OnEnded = func(c *call.CallInfo) {
		if ac, ok := s.reg.get(c.CallID); ok && ac.cm == cm {
			origin := "unknown"
			if value := ac.endOrigin.Load(); value != nil {
				origin = *value
			}
			ended := &callEndDiagnostic{origin, safeEndCode(string(c.StateData.EndReason)), time.Now().UnixMilli()}
			s.diag.lastEnd.Store(ended)
			ac.diagnostic.end.Store(ended)
		}
		s.removeCall(c.CallID, c.StateData.EndReason == core.EndCallReasonUserEnded)
		s.mgr.broker.endCall(c.CallID, string(c.StateData.EndReason))
	}
	cm.OnControl = func(operation, code string) {
		ac, ok := s.reg.get(callID)
		if !ok || ac.cm != cm {
			return
		}
		if operation == "reject" && (code == "provider_acknowledged" || code == "transport_unconfirmed" || code == "ack_timeout" || code == "ack_invalid" || code == "ack_rejected") {
			s.diag.lastControl.Store(&callControlDiagnostic{operation, code, time.Now().UnixMilli()})
			if code == "provider_acknowledged" {
				if origin := ac.rejectOrigin.Load(); origin != nil {
					ac.markEnd(*origin)
				}
			}
		}
	}
	cm.OnPeerAudio = func(pcm16 []float32) {
		ac, ok := s.reg.get(callID)
		if !ok {
			return
		}
		ac.diagnostic.whatsapp.add(time.Now())
		if h := ac.recording.Load(); h != nil {
			h.capture.Add(1, pcm16, time.Now())
		}
		if bridge := ac.bridge.Load(); bridge != nil {
			_ = bridge.WritePCM(pcm16)
		}
	}
}

func (s *Session) startOutgoing(ctx context.Context, peer types.JID, isVideo bool) (string, error) {
	callID := signaling.GenerateCallID()
	cm := s.createCall(callID)
	if cm == nil {
		return "", fmt.Errorf("call_capacity_or_duplicate")
	}
	if err := cm.StartCall(ctx, callID, peer, isVideo); err != nil {
		s.removeCall(callID)
		return "", err
	}
	return callID, nil
}

func (s *Session) callForEvent(from types.JID, data *waBinary.Node) (*activeCall, bool) {
	callID := callIDFromNode(wrapCall(from, data))
	if callID == "" {
		return nil, false
	}
	return s.reg.get(callID)
}

func (s *Session) onIncomingOffer(ctx context.Context, evt *events.CallOffer) {
	// Offline catch-up includes historical offers. Provider time, not local
	// arrival time, determines whether this can ring as a new incoming call.
	// This does not close an admitted call or infer provider termination.
	if historicalOffer(evt.Timestamp, time.Now()) {
		s.diag.historicalOffers.Add(1)
		return
	}
	s.diag.offers.Add(1)
	s.diag.lastOffer.Store(time.Now().UnixMilli())
	node := wrapCall(evt.From, evt.Data)
	callID := callIDFromNode(node)
	if callID == "" {
		return
	}
	if _, exists := s.reg.get(callID); exists {
		return
	}
	if max := s.mgr.maxCalls; max > 0 && s.reg.count() >= max {
		s.diag.capacityRejected.Add(1)
		s.rejectOffer(ctx, node, evt.From)
		return
	}
	var caller *callerIdentity
	if s.client != nil && s.client.Store != nil {
		identityCtx, cancel := context.WithTimeout(ctx, 200*time.Millisecond)
		caller = resolveOfferCaller(identityCtx, evt.BasicCallMeta, s.client.Store.LIDs, s.client.Store.Contacts)
		cancel()
	}
	cm := s.createCallWithCaller(callID, caller)
	if cm == nil {
		return
	}
	cm.HandleCallOffer(ctx, node, evt.From)
}

func historicalOffer(offered, now time.Time) bool {
	return !offered.IsZero() && now.Sub(offered) > 2*time.Minute
}

func (s *Session) rejectOffer(ctx context.Context, node *waBinary.Node, from types.JID) {
	info := signaling.ExtractNodeInfo(node)
	if info == nil {
		return
	}
	creator := wanode.AttrString(info.InnerNode.Attrs, "call-creator")
	if creator == "" {
		creator = from.String()
	}
	reject := signaling.BuildRejectStanza(from, info.CallID, wanode.MustJID(creator))
	_ = wa.NewSocket(s.client).SendNode(ctx, reject)
	s.log.Info("inbound call rejected: session at capacity", "call_id", info.CallID)
}

func (s *Session) handleEvent(rawEvt any) {
	ctx := context.Background()
	switch evt := rawEvt.(type) {
	case *events.Connected:
		if id := s.client.Store.ID; id != nil {
			_ = s.mgr.store.setJID(s.mgr.appCtx, s.id, id.String())
		}
		s.setAuth(AuthSnapshot{State: "open", Paired: true})
	case *events.LoggedOut:
		s.setAuth(AuthSnapshot{State: "logged_out", Paired: false})
	case *events.Disconnected:
		s.setAuth(AuthSnapshot{State: "disconnected", Paired: s.client.Store.ID != nil})
	case *events.CallOffer:
		s.onIncomingOffer(ctx, evt)
	case *events.CallOfferNotice:
		s.diag.offerNotices.Add(1)
	case *events.UnknownCallEvent:
		s.diag.unknownEvents.Add(1)
	case *events.CallAccept:
		if ac, ok := s.callForEvent(evt.From, evt.Data); ok {
			ac.cm.HandleCallAccept(ctx, wrapCall(evt.From, evt.Data), evt.From)
		}
	case *events.CallTransport:
		if ac, ok := s.callForEvent(evt.From, evt.Data); ok {
			ac.cm.HandleCallTransport(ctx, wrapCall(evt.From, evt.Data), evt.From)
		}
	case *events.CallTerminate:
		if ac, ok := s.callForEvent(evt.From, evt.Data); ok {
			ac.markEnd("whatsapp_terminate")
			ac.cm.HandleCallTerminate(wrapCall(evt.From, evt.Data))
		}
	case *events.CallReject:
		if ac, ok := s.callForEvent(evt.From, evt.Data); ok {
			ac.markEnd("whatsapp_reject")
			ac.cm.HandleCallTerminate(wrapCall(evt.From, evt.Data))
		}
	}
}

func (s *Session) connect(ctx context.Context) error {
	if s.client.Store.ID != nil {
		return s.client.Connect()
	}
	return s.startPairing(ctx)
}

func (s *Session) startPairing(ctx context.Context) error {
	s.setAuth(AuthSnapshot{State: "connecting"})
	qrChan, err := s.client.GetQRChannel(ctx)
	if err != nil {
		s.setAuth(AuthSnapshot{State: "pairing_failed"})
		return err
	}
	if err := s.client.Connect(); err != nil {
		s.setAuth(AuthSnapshot{State: "pairing_failed"})
		return err
	}
	go func() {
		for evt := range qrChan {
			switch evt.Event {
			case "code":
				s.log.Info("scan the QR code to pair this session")
				s.setAuth(AuthSnapshot{State: "qr", QR: evt.Code})
				s.mgr.broker.emitSessionQR(s.id, evt.Code)
			case "success":
				if id := s.client.Store.ID; id != nil {
					_ = s.mgr.store.setJID(s.mgr.appCtx, s.id, id.String())
				}
				s.setAuth(AuthSnapshot{State: "open", Paired: true})
			case "timeout":
				s.setAuth(AuthSnapshot{State: "logged_out", Paired: false})
			default:
				if state := pairingFailureState(evt.Event); state != "" {
					s.setAuth(AuthSnapshot{State: state})
				}
			}
		}
	}()
	return nil
}

// Do not surface upstream Error strings: they may contain device details.
func pairingFailureState(event string) string {
	switch event {
	case "err-client-outdated":
		return "client_outdated"
	case "error", "err-unexpected-state", "err-scanned-without-multidevice":
		return "pairing_failed"
	default:
		return ""
	}
}

func (s *Session) setAuth(a AuthSnapshot) {
	s.mu.Lock()
	if a.State == "open" || s.auth.State == "open" {
		s.connectionEpoch++
	}
	s.auth = a
	s.mu.Unlock()
	s.mgr.broker.emitAuthState(s.id, a)
	s.mgr.broker.emitSessionList(s.mgr.infos())
}

func (s *Session) info() SessionInfo {
	s.mu.Lock()
	a := s.auth
	s.mu.Unlock()
	jid := ""
	if id := s.client.Store.ID; id != nil {
		jid = id.String()
	}
	return SessionInfo{ID: s.id, Name: s.name, JID: jid, State: a.State, Paired: a.Paired || jid != ""}
}

func (s *Session) setBridge(callID string, b *Bridge) {
	oldB, found := s.reg.setBridge(callID, b)
	if !found {
		b.Close()
		return
	}
	if oldB != nil {
		oldB.Close()
	}
}

func (s *Session) removeCall(callID string, graceful ...bool) {
	ac, ok := s.reg.remove(callID)
	if !ok {
		return
	}
	if bridge := ac.bridge.Swap(nil); bridge != nil {
		bridge.Close()
	}
	s.mgr.broker.setCallDiagnostic(callID, ac.audioDiagnostic())
	if h := ac.recording.Load(); h != nil {
		h.finish(len(graceful) > 0 && graceful[0] && !ac.interrupted.Load())
	}
}

func (s *Session) terminateCall(callID string, reason core.EndCallReason) {
	ac, ok := s.reg.get(callID)
	if !ok {
		return
	}
	ac.markEnd("server_end")
	ac.launchMu.Lock()
	defer ac.launchMu.Unlock()
	if ac.prepared.Load() && ac.cm.CurrentCall() == nil {
		s.removeCall(callID)
		s.mgr.broker.endCall(callID, "cancelled_before_provider")
		return
	}
	_ = ac.cm.EndCall(context.Background(), reason)
}

func (s *Session) teardownAllCalls(cause ...string) {
	origin := "gateway_shutdown"
	if len(cause) > 0 {
		origin = cause[0]
	}
	for _, ac := range s.reg.drain() {
		ac.markEnd(origin)
		ac.launchMu.Lock()
		ac.interrupted.Store(true)
		_ = ac.cm.EndCall(context.Background(), core.EndCallReasonUserEnded)
		if ac.prepared.Load() && ac.cm.CurrentCall() == nil {
			s.mgr.broker.endCall(ac.id, "cancelled_before_provider")
		}
		if bridge := ac.bridge.Swap(nil); bridge != nil {
			bridge.Close()
		}
		s.mgr.broker.setCallDiagnostic(ac.id, ac.audioDiagnostic())
		if h := ac.recording.Load(); h != nil {
			h.finish(false)
		}
		ac.launchMu.Unlock()
	}
}

func (s *Session) replaceClient(client *whatsmeow.Client) {
	client.SetMediaHTTPClient(unified.NewMediaHTTPClient())
	s.teardownAllCalls()
	s.client.Disconnect()
	s.client = client
	if !s.mgr.nativeGateway.attachMessages(s) && (s.mgr.messageLab == nil || !s.mgr.messageLab.attach(s)) {
		client.AddEventHandler(s.handleEvent)
	}
}

func (s *Session) shutdown() {
	s.teardownAllCalls()
	s.client.Disconnect()
}

func mapStatus(state core.CallState) CallStatus {
	switch state {
	case core.CallStateActive:
		return StatusConnected
	case core.CallStateEnded:
		return StatusEnded
	case core.CallStateInitiating:
		return StatusStarting
	default:
		return StatusRinging
	}
}
