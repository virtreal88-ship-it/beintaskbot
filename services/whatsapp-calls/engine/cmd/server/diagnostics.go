package main

// Private, authenticated health metadata. Never expose identities, packet bytes,
// transcripts, keys, or upstream error strings through this snapshot.
import (
	"sync/atomic"
	"time"

	"wacalls/internal/recording"
)

type callDiagnostics struct {
	offers           atomic.Uint64
	offerNotices     atomic.Uint64
	unknownEvents    atomic.Uint64
	lastOffer        atomic.Int64
	capacityRejected atomic.Uint64
}

type recordingQuality struct {
	DurationMS int    `json:"duration_ms"`
	Samples    [2]int `json:"samples_received"`
	NonSilent  [2]int `json:"non_silent_samples"`
	Gaps       [2]int `json:"arrival_gaps_over_200ms"`
	Complete   bool   `json:"complete"`
	EndCode    string `json:"end_code"`
}

func qualityOf(m recording.Manifest) *recordingQuality {
	return &recordingQuality{m.DurationMS, m.Samples, m.NonSilent, m.Gaps, m.Complete, m.EndCode}
}

func (s *Session) transportReady() bool {
	return s.client != nil && s.client.IsConnected() && s.client.IsLoggedIn()
}

func (s *Session) diagnostics() map[string]any {
	out := map[string]any{"transport_connected": s.client != nil && s.client.IsConnected(),
		"logged_in": s.client != nil && s.client.IsLoggedIn(), "offer_events": s.diag.offers.Load(),
		"offer_notice_events": s.diag.offerNotices.Load(), "unknown_call_events": s.diag.unknownEvents.Load(),
		"last_offer_at_ms": s.diag.lastOffer.Load(), "capacity_rejections": s.diag.capacityRejected.Load(),
		"observed_at_ms": time.Now().UnixMilli()}
	var attached, started, input, output uint64
	s.reg.mu.Lock()
	out["active_registry_calls"] = len(s.reg.calls)
	for _, ac := range s.reg.calls {
		if b := ac.bridge.Load(); b != nil && b.stream != nil {
			if b.stream.attached.Load() {
				attached++
			}
			if b.stream.startRequested.Load() {
				started++
			}
			input += b.stream.inputFrames.Load()
			output += b.stream.outputFrames.Load()
		}
	}
	s.reg.mu.Unlock()
	out["audio_attached"] = attached
	out["provider_start_requested"] = started
	out["input_frames"] = input
	out["output_frames"] = output
	if s.mgr.recordings != nil {
		out["last_recording_quality"] = s.mgr.recordings.lastQuality.Load()
	}
	return out
}
