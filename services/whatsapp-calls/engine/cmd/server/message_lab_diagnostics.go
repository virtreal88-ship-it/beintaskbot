//go:build unifiedlab

package main

import (
	"errors"
	"sync"

	waBinary "go.mau.fi/whatsmeow/binary"
	waLog "go.mau.fi/whatsmeow/util/log"
	"wacalls/internal/unified"
)

// Only finite, code-defined counters. Never format or retain a provider message,
// argument, ID, stanza, stack, transcript, or media value, even for unknown errors.
type labDiagnostics struct {
	mu     sync.Mutex
	counts map[string]uint64
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
}

// NewClient snapshots its child loggers. Install this before construction so
// receive-frame counters cover the path before parsing/decryption as well.
func (m *messageLab) providerLogger(fallback waLog.Logger) waLog.Logger {
	if m == nil {
		return fallback
	}
	return labProviderLogger{diagnostics: &m.diagnostics}
}

func (l labProviderLogger) record(level, format string, args []any) {
	if l.receive && level == "debug" && format == "%s" && len(args) == 1 {
		code := "provider_frame_other"
		if node, ok := args[0].(*waBinary.Node); ok && node != nil {
			switch node.Tag {
			case "message", "appdata", "receipt", "call", "notification", "success", "failure", "iq", "ib", "ack", "stream:error", "xmlstreamend":
				code = "provider_frame_" + node.Tag
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
func (l labProviderLogger) Errorf(f string, a ...any) { l.record("error", f, a) }
func (l labProviderLogger) Warnf(f string, a ...any)  { l.record("warning", f, a) }
func (l labProviderLogger) Infof(f string, a ...any)  { l.record("info", f, a) }
func (l labProviderLogger) Debugf(f string, a ...any) { l.record("debug", f, a) }
func (l labProviderLogger) Sub(module string) waLog.Logger {
	return labProviderLogger{diagnostics: l.diagnostics, receive: module == "Recv"}
}
