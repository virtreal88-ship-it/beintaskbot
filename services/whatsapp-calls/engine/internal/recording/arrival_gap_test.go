package recording

import (
	"testing"
	"time"
)

// An arrival gap is not proof that speech packets were lost. All samples can
// eventually arrive after a network stall; the current wall-clock recorder still
// inserts silence and conservatively marks the resulting recording incomplete.
func TestRecoveredBurstStillHasArrivalGapWithNoLostSamples(t *testing.T) {
	now := time.Date(2026, 11, 18, 0, 0, 0, 0, time.UTC)
	c := NewManaged("s", "c", "a", "p", now)
	pcm := make([]float32, 320)
	for i := range pcm {
		pcm[i] = .2
	}
	for i := 1; i <= 50; i++ {
		at := now.Add(time.Duration(i) * 20 * time.Millisecond)
		c.Add(0, pcm, at)
		if i >= 6 && i <= 25 {
			at = now.Add(500 * time.Millisecond)
		}
		c.Add(1, pcm, at)
	}
	_, m, err := c.Finish(now.Add(1400*time.Millisecond), true, make([]byte, 32))
	if err != nil {
		t.Fatal(err)
	}
	if m.Samples != [2]int{16000, 16000} || m.Gaps[0] != 0 || m.Gaps[1] != 1 || m.Complete || m.EndCode != "audio_gaps" {
		t.Fatalf("unexpected gap evidence: %+v", m)
	}
}
