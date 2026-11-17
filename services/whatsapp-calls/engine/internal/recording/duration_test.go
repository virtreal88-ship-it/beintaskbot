package recording

import (
	"testing"
	"time"
)

func TestManagedFullFifteenMinutesBothTracks(t *testing.T) {
	start := time.Now()
	c := NewManaged("synthetic", "synthetic", "123456@s.whatsapp.net", "987654@lid", start)
	pcm := make([]float32, 320)
	for i := range pcm {
		pcm[i] = .25
	}
	for frame := 0; frame < 900*50; frame++ {
		now := start.Add(time.Duration(frame+1) * 20 * time.Millisecond)
		c.Add(0, pcm, now)
		c.Add(1, pcm, now)
	}
	raw, m, err := c.Finish(start.Add(900*time.Second), true, make([]byte, 32))
	if err != nil || m.DurationMS != 900000 || !m.Complete || m.Samples != [2]int{14400000, 14400000} || len(raw) < 57600044 {
		t.Fatal("15 minute stereo capture truncated", m.DurationMS, m.Samples, err)
	}
	t.Logf("synthetic signed bundle bytes=%d duration_ms=%d", len(raw), m.DurationMS)
}

func TestLegacyCaptureRemainsTwoMinutes(t *testing.T) {
	start := time.Now()
	c := New("s", "c", "a", "p", start)
	c.Add(0, []float32{.2}, start.Add(121*time.Second))
	_, m, err := c.Finish(start.Add(121*time.Second), true, make([]byte, 32))
	if err != nil || m.DurationMS != 120000 || m.EndCode != "duration_limit" {
		t.Fatal("legacy limit changed")
	}
}
