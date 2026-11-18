//go:build unifiedlab

package main

import (
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"testing"

	waBinary "go.mau.fi/whatsmeow/binary"
	"go.mau.fi/whatsmeow/types"
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

func TestCallControlDiagnosticsRetainOnlyStructure(t *testing.T) {
	m := &messageLab{}
	l := m.providerLogger(waLog.Noop)
	secret := "secret-identifier"
	peer := types.JID{User: secret, Server: types.HiddenUserServer, Device: 4}
	creator := peer.ToNonAD()
	l.Sub("Recv").Debugf("%s", &waBinary.Node{Tag: "call", Attrs: waBinary.Attrs{"from": peer, "id": secret}, Content: []waBinary.Node{{Tag: "offer", Attrs: waBinary.Attrs{"call-creator": creator, "call-id": secret}}}})
	l.Sub("Send").Debugf("%s", &waBinary.Node{Tag: "call", Attrs: waBinary.Attrs{"to": peer, "id": secret}, Content: []waBinary.Node{{Tag: "reject", Attrs: waBinary.Attrs{"call-creator": creator, "call-id": secret}}}})
	for _, code := range []any{nil, 403, secret} {
		attrs := waBinary.Attrs{"class": "call", "type": "reject", "id": secret}
		if code != nil {
			attrs["error"] = code
		}
		l.Sub("Recv").Debugf("%s", &waBinary.Node{Tag: "ack", Attrs: attrs, Content: []byte(secret)})
	}
	counts := m.diagnostics.snapshot()
	raw, _ := json.Marshal(counts)
	if strings.Contains(string(raw), secret) || counts["offer_route_lid"] != 1 || counts["offer_route_device"] != 1 || counts["reject_route_device"] != 1 || counts["reject_ack_error_absent"] != 1 || counts["reject_ack_error_403"] != 1 || counts["reject_ack_error_other"] != 1 || counts["reject_explicit_from"] != 0 || counts["offer_route_creator_equal"] != 0 {
		t.Fatal("unsafe/missing structural counters")
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

func TestLabActivationEvidenceRequiresCurrentMatchingResponse(t *testing.T) {
	d := &labDiagnostics{}
	l := labProviderLogger{diagnostics: d}
	send, recv := l.Sub("Send"), l.Sub("Recv")
	request := func(id string) {
		send.Debugf("%s", &waBinary.Node{Tag: "iq", Attrs: waBinary.Attrs{"id": id, "xmlns": "passive", "type": "set"}, Content: []waBinary.Node{{Tag: "active"}}})
	}
	response := func(id, kind string) {
		recv.Debugf("%s", &waBinary.Node{Tag: "iq", Attrs: waBinary.Attrs{"id": id, "type": kind}, Content: []byte("secret-provider-content")})
	}
	request("secret-old-id")
	recv.Debugf("%s", &waBinary.Node{Tag: "success"})
	response("secret-old-id", "result")
	request("secret-current-id")
	response("secret-unrelated-id", "result")
	response("secret-current-id", "unknown")
	if d.snapshot()["activation_acknowledged"] != 0 {
		t.Fatal("unrelated, old or unknown response established activation")
	}
	response("secret-current-id", "result")
	response("secret-current-id", "result")
	request("secret-next-id")
	response("secret-next-id", "error")
	recv.Debugf("%s", &waBinary.Node{Tag: "ib", Content: []waBinary.Node{{Tag: "offline_preview"}, {Tag: "offline"}, {Tag: "secret-unknown-tag"}}})
	counts := d.snapshot()
	raw, _ := json.Marshal(counts)
	if strings.Contains(string(raw), "secret") || counts["activation_requested"] != 3 || counts["activation_acknowledged"] != 1 || counts["activation_rejected"] != 1 || counts["provider_ib_offline"] != 1 || counts["provider_ib_offline_preview"] != 1 {
		t.Fatal("connection evidence leaked content or accepted incorrect response")
	}
	if counts["provider_call_offer"] != 0 || counts["provider_frame_message"] != 0 {
		t.Fatal("activation was mislabeled as inbound delivery")
	}
}
