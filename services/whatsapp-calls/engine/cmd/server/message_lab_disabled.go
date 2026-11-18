//go:build !unifiedlab

package main

import (
	"context"
	"database/sql"
	"net/http"
)

// Production builds do not contain the experimental messaging endpoints or flag.
type messageLab struct{}

func newMessageLab(context.Context, *sql.DB, string) (*messageLab, error) { return nil, nil }
func (*messageLab) attach(*Session) bool                                  { return false }
func (*server) registerMessageLab(*http.ServeMux)                         {}
