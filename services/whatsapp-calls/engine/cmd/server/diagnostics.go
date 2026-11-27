package main

// Private, authenticated health metadata. Never expose identities, packet bytes,
// transcripts, keys, or upstream error strings through this snapshot.
import (
	"sync"
	"sync/atomic"
	"time"

	"wacalls/internal/recording"
)

type callDiagnostics struct {
	offers           atomic.Uint64
	historicalOffers atomic.Uint64
	offerNotices     atomic.Uint64
	unknownEvents    atomic.Uint64
	lastOffer        atomic.Int64
	capacityRejected atomic.Uint64
	lastEnd          atomic.Pointer[callEndDiagnostic]
	lastStream       atomic.Pointer[streamEndDiagnostic]
	lastControl      atomic.Pointer[callControlDiagnostic]
}

type callControlDiagnostic struct {
	Operation string `json:"operation"`
	Code      string `json:"code"`
	AtMS      int64  `json:"at_ms"`
}

type callEndDiagnostic struct {
	Origin string `json:"origin"`
	Code   string `json:"code"`
	AtMS   int64  `json:"at_ms"`
}

type streamEndDiagnostic struct {
	Code         string `json:"code"`
	InputFrames  uint64 `json:"input_frames"`
	OutputFrames uint64 `json:"output_frames"`
	AtMS         int64  `json:"at_ms"`
}

func safeEndCode(reason string) string {
	switch reason {
	case "user_ended", "declined", "timeout", "busy", "cancelled", "failed", "do_not_disturb":
		return reason
	default:
		return "unknown"
	}
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
		"historical_offers_ignored": s.diag.historicalOffers.Load(),
		"last_offer_at_ms":          s.diag.lastOffer.Load(), "capacity_rejections": s.diag.capacityRejected.Load(),
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
	out["last_call_end"] = s.diag.lastEnd.Load()
	out["last_stream_end"] = s.diag.lastStream.Load()
	out["last_control_signal"] = s.diag.lastControl.Load()
	if s.mgr.recordings != nil {
		out["last_recording_quality"] = s.mgr.recordings.lastQuality.Load()
	}
	return out
}

// Counters measure PCM arrivals at the engine, including beyond the recording
// retention limit. No sample values or identities enter these snapshots.
type audioArrival struct {
	mu          sync.Mutex
	last        time.Time
	Frames      uint64 `json:"frames"`
	Gaps        uint64 `json:"gaps_over_200ms"`
	MaxGapMS    int64  `json:"max_gap_ms"`
	LastGapAtMS int64  `json:"last_gap_at_ms"`
}
type audioArrivalSnapshot struct {
	Frames        uint64 `json:"frames"`
	Gaps          uint64 `json:"gaps_over_200ms"`
	MaxGapMS      int64  `json:"max_gap_ms"`
	LastGapAtMS   int64  `json:"last_gap_at_ms"`
	LastFrameAtMS int64  `json:"last_frame_at_ms"`
}

func (a *audioArrival) add(now time.Time) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if !a.last.IsZero() {
		gap := now.Sub(a.last).Milliseconds()
		if gap > 200 {
			a.Gaps++
			if gap > a.MaxGapMS {
				a.MaxGapMS = gap
			}
			a.LastGapAtMS = now.UnixMilli()
		}
	}
	a.last = now
	a.Frames++
}
func (a *audioArrival) snapshot() audioArrivalSnapshot {
	a.mu.Lock()
	defer a.mu.Unlock()
	last := int64(0)
	if !a.last.IsZero() {
		last = a.last.UnixMilli()
	}
	return audioArrivalSnapshot{a.Frames, a.Gaps, a.MaxGapMS, a.LastGapAtMS, last}
}

type callAudioDiagnostic struct {
	browser  audioArrival
	whatsapp audioArrival
	end      atomic.Pointer[callEndDiagnostic]
	stream   atomic.Pointer[streamEndDiagnostic]
}
type perCallDiagnostic struct {
	Version  int                  `json:"version"`
	AtMS     int64                `json:"at_ms"`
	End      *callEndDiagnostic   `json:"end,omitempty"`
	Stream   *streamEndDiagnostic `json:"stream,omitempty"`
	Browser  audioArrivalSnapshot `json:"browser_to_engine"`
	WhatsApp audioArrivalSnapshot `json:"whatsapp_to_engine"`
}

func (a *activeCall) audioDiagnostic() *perCallDiagnostic {
	end := a.diagnostic.end.Load()
	if end == nil {
		if origin := a.endOrigin.Load(); origin != nil {
			end = &callEndDiagnostic{*origin, "unknown", time.Now().UnixMilli()}
		}
	}
	return &perCallDiagnostic{1, time.Now().UnixMilli(), end, a.diagnostic.stream.Load(),
		a.diagnostic.browser.snapshot(), a.diagnostic.whatsapp.snapshot()}
}
