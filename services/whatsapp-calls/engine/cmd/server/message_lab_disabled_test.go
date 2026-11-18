//go:build !unifiedlab

package main

import (
	"flag"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestProductionBuildDoesNotExposeUnifiedExperiment(t *testing.T) {
	if flag.Lookup("unified-lab-config") != nil {
		t.Fatal("lab flag in production binary")
	}
	mux := http.NewServeMux()
	s := &server{}
	s.registerMessageLab(mux)
	for _, path := range []string{"/api/unified-lab/events", "/api/unified-lab/text", "/unified-lab"} {
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, httptest.NewRequest("GET", "http://127.0.0.1"+path, nil))
		if w.Code != 404 {
			t.Fatal("lab route in production binary")
		}
	}
}
