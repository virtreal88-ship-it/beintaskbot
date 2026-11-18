package recording

import (
	"testing"
	"time"
)

func TestExtendedTestKeeps24HourRetentionAndStrictFourMinuteBound(t *testing.T) {
	start := time.Now()
	c := NewExtendedTest("test", "call", "account", "peer", start)
	if c.maxSamples != Rate*240 || c.manifest.ExpiresAt != start.Add(24*time.Hour).UTC().Format(time.RFC3339Nano) {
		t.Fatal("unbounded test capture")
	}
	c.Add(0, []float32{0.5}, start.Add(180*time.Second))
	if c.truncated || c.manifest.NonSilent[0] != 1 {
		t.Fatal("three minute test truncated")
	}
	c.Add(0, []float32{0.5}, start.Add(241*time.Second))
	if !c.truncated || len(c.tracks[0]) > Rate*240 {
		t.Fatal("test exceeds bound")
	}
	if New("s", "c", "a", "p", start).maxSamples != Rate*120 || NewManaged("s", "c", "a", "p", start).maxSamples != Rate*900 {
		t.Fatal("other bounds changed")
	}
}
