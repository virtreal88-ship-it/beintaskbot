//go:build unifiedlab

package main

import (
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"testing"

	"wacalls/internal/unified"
)

func TestLabProviderDiagnosticsNeverRetainContent(t *testing.T) {
	d := &labDiagnostics{}
	l := labProviderLogger{d}
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
