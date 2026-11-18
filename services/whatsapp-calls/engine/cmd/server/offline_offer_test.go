package main

import (
	"context"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"testing"
	"time"
)

func TestHistoricalOfferDoesNotAllocateCallOrSignalProvider(t *testing.T) {
	// No manager, registry or provider: touching any would fail this test.
	s := &Session{}
	s.onIncomingOffer(context.Background(), &events.CallOffer{BasicCallMeta: types.BasicCallMeta{Timestamp: time.Now().Add(-time.Hour)}})
	if s.diag.offers.Load() != 0 || s.diag.lastOffer.Load() != 0 || s.diag.historicalOffers.Load() != 1 {
		t.Fatal("historical offer became a live incoming call")
	}
}

func TestFreshOrMissingOfferTimePreservesExistingCallHandling(t *testing.T) {
	now := time.Now()
	for _, offered := range []time.Time{now, now.Add(-time.Minute), now.Add(-2 * time.Minute), now.Add(time.Second), {}} {
		if historicalOffer(offered, now) {
			t.Fatal("fresh/unknown offer discarded")
		}
	}
	if !historicalOffer(now.Add(-2*time.Minute-time.Second), now) {
		t.Fatal("old offer accepted")
	}
}
