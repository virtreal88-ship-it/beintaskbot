//go:build unifiedlab

package main

import waLog "go.mau.fi/whatsmeow/util/log"

func (m *messageLab) providerLogger(fallback waLog.Logger) waLog.Logger {
	if m == nil {
		return fallback
	}
	return labProviderLogger{diagnostics: &m.diagnostics}
}
