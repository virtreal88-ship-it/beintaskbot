package main

import (
	"context"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"time"
	"wacalls/internal/unified"
)

func (g *appGateway) attachMessages(s *Session) bool {
	if g == nil || g.messages == nil {
		return false
	}
	client := s.client
	client.Log = labProviderLogger{diagnostics: &g.providerDiagnostics}
	client.PrePairCallback = func(jid types.JID, _, _ string) bool { return jid.ToNonAD().String() == g.cfg.Account }
	client.SynchronousAck = true
	client.EnableDecryptedEventBuffer = true
	unified.ProtectDevice(client.Store, g.messages, g.cfg.Account)
	catchup := g.newOfflineCatchup(s)
	client.AddEventHandlerWithSuccessStatus(func(event any) bool {
		if s.client != client {
			if catchup != nil {
				catchup.stop(false)
			}
			return true
		}
		if client.Store.ID != nil && client.Store.ID.ToNonAD().String() != g.cfg.Account {
			go client.Disconnect()
			return false
		}
		g.observeProviderEvent(event)
		stored := g.messages.Capture(s.mgr.appCtx, event, client)
		catchup.observe(s.mgr.appCtx, event, stored)
		if stored {
			g.providerDiagnostics.add("capture_handler_succeeded")
		} else {
			g.providerDiagnostics.add("capture_failed")
		}
		if stored && g.groups != nil {
			var chat types.JID
			switch e := event.(type) {
			case *events.Message:
				chat = e.Info.Chat
			case *events.GroupInfo:
				chat = e.JID
			}
			if chat.Server == types.GroupServer {
				select {
				case g.groups.queue <- chat:
				default:
				}
			}
		}
		s.handleEvent(event)
		return stored
	})
	return true
}

func (g *appGateway) deliverMessages(ctx context.Context) {
	if g == nil || g.messages == nil {
		return
	}
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	var retryOnTick bool
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			retryOnTick = false
		case <-g.messages.DeliveryReady():
		}
		if retryOnTick {
			continue
		}
		for i := 0; i < 16; i++ {
			delivered, err := g.messages.DeliverOnce(ctx, g.client, g.cfg.Endpoint+"/messages", g.cfg.Token)
			if err != nil {
				retryOnTick = true
				break
			}
			if !delivered {
				break
			}
		}
	}
}

// Counters are local to this source process. No payload or identifier is retained.
func (g *appGateway) observeProviderEvent(event any) {
	code := "event_other"
	switch event.(type) {
	case *events.Message:
		code = "event_message"
	case *events.Receipt:
		code = "event_receipt"
	case *events.UndecryptableMessage:
		code = "event_undecryptable"
	case *events.Connected:
		code = "event_connected"
	case *events.Disconnected:
		code = "event_disconnected"
	case *events.LoggedOut:
		code = "event_logged_out"
	case *events.StreamReplaced:
		code = "event_stream_replaced"
	case *events.OfflineSyncPreview:
		code = "event_offline_preview"
	case *events.OfflineSyncCompleted:
		code = "event_offline_completed"
	}
	g.providerDiagnostics.add(code)
}
