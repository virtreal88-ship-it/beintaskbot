package main

import (
	"errors"
	"strconv"
	"sync"

	waBinary "go.mau.fi/whatsmeow/binary"
	"go.mau.fi/whatsmeow/types"
	waLog "go.mau.fi/whatsmeow/util/log"
	"wacalls/internal/unified"
)

// Only finite, code-defined counters. Never format or retain a provider message,
// argument, ID, stanza, stack, transcript, or media value, even for unknown errors.
type labDiagnostics struct {
	mu     sync.Mutex
	counts map[string]uint64
	// One ephemeral correlation value, never included in snapshots or logs.
	activationID string
}

func (d *labDiagnostics) once(code string) bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.counts == nil {
		d.counts = map[string]uint64{}
	}
	if d.counts[code] != 0 {
		return false
	}
	d.counts[code] = 1
	return true
}

func (d *labDiagnostics) add(code string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.counts == nil {
		d.counts = map[string]uint64{}
	}
	d.counts[code]++
}
func (d *labDiagnostics) snapshot() map[string]uint64 {
	d.mu.Lock()
	defer d.mu.Unlock()
	out := make(map[string]uint64, len(d.counts))
	for k, v := range d.counts {
		out[k] = v
	}
	return out
}

type labProviderLogger struct {
	diagnostics *labDiagnostics
	receive     bool
	send        bool
}

// NewClient snapshots its child loggers. Install this before construction so
// receive-frame counters cover the path before parsing/decryption as well.

func (l labProviderLogger) record(level, format string, args []any) {
	if (l.receive || l.send) && level == "debug" && format == "%s" && len(args) == 1 {
		if node, ok := args[0].(*waBinary.Node); ok && node != nil {
			l.callShape(node)
			l.connectionShape(node)
		}
	}
	if l.receive && level == "debug" && format == "%s" && len(args) == 1 {
		code := "provider_frame_other"
		if node, ok := args[0].(*waBinary.Node); ok && node != nil {
			switch node.Tag {
			case "message", "appdata", "receipt", "call", "notification", "success", "failure", "iq", "ib", "ack", "stream:error", "xmlstreamend":
				code = "provider_frame_" + node.Tag
			}
			if node.Tag == "call" {
				kind := "other"
				children := node.GetChildren()
				if len(children) == 1 {
					switch children[0].Tag {
					case "offer", "offer_notice", "accept", "preaccept", "transport", "terminate", "reject", "relaylatency":
						kind = children[0].Tag
					}
				}
				l.diagnostics.add("provider_call_" + kind)
			}
		}
		l.diagnostics.add(code)
		return
	}
	code := "provider_" + level
	switch format {
	case "Decrypting message from %s":
		code = "provider_decrypt_started"
	case "Error decrypting message %s from %s: %v":
		code = "provider_decrypt_failed"
	case "Ignoring message %s from %s: %v":
		code = "provider_decrypt_previously_processed"
	case "Failed to parse receipt: %v":
		code = "provider_receipt_parse_failed"
	case "Failed to send post-connect passive IQ: %v":
		code = "provider_activation_failed"
	case "Event handler panicked while handling a %T: %v\n%s":
		code = "provider_handler_panicked"
	case "Failed to save device after updating LID: %v":
		code = "provider_device_save_failed"
	case "Failed to decompress frame: %v", "Failed to decode node in frame: %v":
		code = "provider_frame_parse_failed"
	case "Node handling is taking long for %s (started %s ago)", "Node handling took %s for %s", "Continuing handling of %s in background as it's taking too long":
		code = "provider_handler_delayed"
	}
	l.diagnostics.add(code)
	for _, arg := range args {
		if err, ok := arg.(error); ok && errors.Is(err, unified.ErrConfiguration) {
			l.diagnostics.add("provider_buffer_configuration_failed")
			break
		}
	}
}

// Authentication and keepalive alone do not establish inbound delivery. Observe
// the SDK's existing activation exchange; do not send extra protocol messages.
func (l labProviderLogger) connectionShape(node *waBinary.Node) {
	d := l.diagnostics
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.counts == nil {
		d.counts = map[string]uint64{}
	}
	if l.receive && node.Tag == "success" {
		d.activationID = "" // Never correlate an old connection's response.
	}
	if node.Tag == "iq" {
		id, _ := node.Attrs["id"].(string)
		if l.send && node.Attrs["xmlns"] == "passive" && node.Attrs["type"] == "set" {
			children := node.GetChildren()
			if id != "" && len(children) == 1 && children[0].Tag == "active" {
				d.activationID = id
				d.counts["activation_requested"]++
			}
		}
		if l.receive && id != "" && id == d.activationID {
			switch node.Attrs["type"] {
			case "result":
				d.counts["activation_acknowledged"]++
				d.activationID = ""
			case "error":
				d.counts["activation_rejected"]++
				d.activationID = ""
			}
		}
	}
	if l.receive && node.Tag == "ib" {
		for _, child := range node.GetChildren() {
			switch child.Tag {
			case "offline_preview", "offline", "dirty":
				d.counts["provider_ib_"+child.Tag]++
			}
		}
	}
}
func (l labProviderLogger) Errorf(f string, a ...any) { l.record("error", f, a) }
func (l labProviderLogger) Warnf(f string, a ...any)  { l.record("warning", f, a) }
func (l labProviderLogger) Infof(f string, a ...any)  { l.record("info", f, a) }
func (l labProviderLogger) Debugf(f string, a ...any) { l.record("debug", f, a) }
func (l labProviderLogger) Sub(module string) waLog.Logger {
	return labProviderLogger{diagnostics: l.diagnostics, receive: module == "Recv", send: module == "Send"}
}

// Structural counters only: never retain or format the node or any identifier.
func (l labProviderLogger) callShape(node *waBinary.Node) {
	if l.receive && node.Tag == "ack" {
		class, _ := node.Attrs["class"].(string)
		switch class {
		case "call", "receipt", "message":
		default:
			class = "other"
		}
		action, _ := node.Attrs["type"].(string)
		switch action {
		case "offer", "preaccept", "accept", "reject", "terminate", "":
		default:
			action = "other"
		}
		if action == "" {
			action = "absent"
		}
		l.diagnostics.add("ack_" + class + "_" + action)
	}
	if l.receive && node.Tag == "ack" && node.Attrs["class"] == "call" && node.Attrs["type"] == "reject" {
		code := "absent"
		switch v := node.Attrs["error"].(type) {
		case string:
			code = v
		case int:
			code = strconv.Itoa(v)
		case int64:
			code = strconv.FormatInt(v, 10)
		case nil:
		default:
			code = "other"
		}
		switch code {
		case "absent", "0", "400", "401", "403", "404", "409", "429", "439", "500", "503":
		default:
			code = "other"
		}
		l.diagnostics.add("reject_ack_error_" + code)
		return
	}
	children := node.GetChildren()
	if node.Tag != "call" || len(children) != 1 {
		return
	}
	child := children[0]
	if l.send && child.Tag == "preaccept" {
		l.diagnostics.add("sent_preaccept")
	}
	prefix := ""
	if l.receive && child.Tag == "offer" {
		prefix = "offer"
	} else if l.send && child.Tag == "reject" {
		prefix = "reject"
	} else {
		return
	}
	attr := "to"
	if l.receive {
		attr = "from"
	}
	peer, peerOK := node.Attrs[attr].(types.JID)
	creator, creatorOK := child.Attrs["call-creator"].(types.JID)
	for _, item := range []struct {
		name string
		jid  types.JID
		ok   bool
	}{{"route", peer, peerOK}, {"creator", creator, creatorOK}} {
		ns := "unknown"
		if item.ok {
			switch item.jid.Server {
			case types.HiddenUserServer:
				ns = "lid"
			case types.DefaultUserServer:
				ns = "pn"
			}
		}
		l.diagnostics.add(prefix + "_" + item.name + "_" + ns)
		if item.ok && item.jid.Device != 0 {
			l.diagnostics.add(prefix + "_" + item.name + "_device")
		}
	}
	if peerOK && creatorOK && peer == creator {
		l.diagnostics.add(prefix + "_route_creator_equal")
	}
	if _, ok := node.Attrs["from"]; l.send && ok {
		l.diagnostics.add("reject_explicit_from")
	}
}
