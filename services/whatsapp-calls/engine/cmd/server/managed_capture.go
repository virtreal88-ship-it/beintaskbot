package main

import (
	"context"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"time"
	"wacalls/internal/unified"
)

func (g *appGateway) attachMessages(s *Session) bool {
	if g == nil || g.messages == nil {
		return false
	}
	client := s.client
	client.Log = waLog.Noop
	client.PrePairCallback = func(jid types.JID, _, _ string) bool { return jid.ToNonAD().String() == g.cfg.Account }
	client.SynchronousAck = true
	client.EnableDecryptedEventBuffer = true
	unified.ProtectDevice(client.Store, g.messages, g.cfg.Account)
	client.AddEventHandlerWithSuccessStatus(func(event any) bool {
		if s.client != client {
			return true
		}
		if client.Store.ID != nil && client.Store.ID.ToNonAD().String() != g.cfg.Account {
			go client.Disconnect()
			return false
		}
		stored := g.messages.Capture(s.mgr.appCtx, event)
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
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		for i := 0; i < 16; i++ {
			delivered, err := g.messages.DeliverOnce(ctx, g.client, g.cfg.Endpoint+"/messages", g.cfg.Token)
			if err != nil || !delivered {
				break
			}
		}
	}
}
