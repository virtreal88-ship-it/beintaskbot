//go:build unifiedlab

package main

import (
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"testing"

	waBinary "go.mau.fi/whatsmeow/binary"
	waLog "go.mau.fi/whatsmeow/util/log"
	"wacalls/internal/unified"
)

func TestLabProviderDiagnosticsNeverRetainContent(t *testing.T) {
	d := &labDiagnostics{}
	l := labProviderLogger{diagnostics: d}
	secret := "private-key transcript person@example.invalid 15550000001"
	l.Errorf(secret, secret)
	l.Warnf("Error decrypting message %s from %s: %v", secret, secret, errors.Join(unified.ErrConfiguration, errors.New(secret)))
	l.Sub(secret).Infof(secret, secret)
	var wg sync.WaitGroup
	for n := 0; n < 20; n++ {
		wg.Add(1)
		go func() { defer wg.Done(); l.Debugf("Decrypting message from %s", secret) }()
	}
	wg.Wait()
	values := d.snapshot()
	raw, _ := json.Marshal(values)
	if strings.Contains(string(raw), "private") || strings.Contains(string(raw), "155500") || values["provider_decrypt_started"] != 20 || values["provider_buffer_configuration_failed"] != 1 || values["provider_decrypt_failed"] != 1 {
		t.Fatal("diagnostic counters unsafe or incorrect")
	}
	values["provider_decrypt_started"] = 0
	if d.snapshot()["provider_decrypt_started"] != 20 {
		t.Fatal("snapshot aliases internal state")
	}
}

func TestLabFrameDiagnosticsOnlyRetainAllowedType(t *testing.T) {
	m := &messageLab{}
	l := m.providerLogger(waLog.Noop).Sub("Recv")
	secret := "private transcript identifier"
	l.Debugf("%s", &waBinary.Node{Tag: "message", Attrs: waBinary.Attrs{"id": secret}, Content: []byte(secret)})
	l.Debugf("%s", &waBinary.Node{Tag: secret, Content: []byte(secret)})
	l.Debugf("%s", secret)
	m.providerLogger(waLog.Noop).Sub("Send").Debugf("%s", &waBinary.Node{Tag: "message"})
	counts := m.diagnostics.snapshot()
	raw, _ := json.Marshal(counts)
	if strings.Contains(string(raw), "private") || counts["provider_frame_message"] != 1 || counts["provider_frame_other"] != 2 {
		t.Fatal("frame diagnostic leaked or misclassified")
	}
	var disabled *messageLab
	if disabled.providerLogger(waLog.Noop) != waLog.Noop {
		t.Fatal("disabled lab changed logger")
	}
}

func TestLabCallFramesSeparateOfferFromNoticeWithoutContent(t *testing.T) {
	m := &messageLab{}
	l := m.providerLogger(waLog.Noop).Sub("Recv")
	secret := "private-call-id-private-key"
	for _, kind := range []string{"offer", "offer_notice", "terminate", secret} {
		l.Debugf("%s", &waBinary.Node{Tag: "call", Attrs: waBinary.Attrs{"id": secret}, Content: []waBinary.Node{
			{Tag: kind, Attrs: waBinary.Attrs{"call-id": secret}, Content: []byte(secret)},
		}})
	}
	// Multiple children are not an actionable offer in the pinned SDK.
	l.Debugf("%s", &waBinary.Node{Tag: "call", Content: []waBinary.Node{{Tag: "offer"}, {Tag: "offer_notice"}}})
	counts := m.diagnostics.snapshot()
	raw, _ := json.Marshal(counts)
	if strings.Contains(string(raw), "private") || counts["provider_frame_call"] != 5 || counts["provider_call_offer"] != 1 || counts["provider_call_offer_notice"] != 1 || counts["provider_call_terminate"] != 1 || counts["provider_call_other"] != 2 {
		t.Fatal("unsafe or ambiguous call diagnostics")
	}
}
