//go:build !unifiedlab

package main

import (
	"context"
	"database/sql"
	waLog "go.mau.fi/whatsmeow/util/log"
	"net/http"
)

// Production builds do not contain the experimental messaging endpoints or flag.
type messageLab struct{}

func (*messageLab) providerLogger(fallback waLog.Logger) waLog.Logger { return fallback }

func newMessageLab(context.Context, *sql.DB, string) (*messageLab, error) { return nil, nil }
func (*messageLab) attach(*Session) bool                                  { return false }
func (*server) registerMessageLab(*http.ServeMux)                         {}
func (*server) guardMessageLab(next http.Handler) http.Handler            { return next }
