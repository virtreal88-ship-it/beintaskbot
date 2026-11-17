package recording

import (
	"testing"
	"time"
)

func TestManagedRetentionDoesNotExtendOldPilot(t *testing.T) {
	start := time.Date(2026, 11, 18, 12, 0, 0, 0, time.UTC)
	for _, tc := range []struct {
		capture *Capture
		days    int
	}{
		{New("s", "c", "a", "p", start), 1}, {NewManaged("s", "c", "a", "p", start), 30},
	} {
		tc.capture.Add(0, []float32{.1}, start)
		tc.capture.Add(1, []float32{.1}, start)
		_, m, e := tc.capture.Finish(start.Add(time.Second), true, make([]byte, 32))
		if e != nil {
			t.Fatal(e)
		}
		expiry, e := time.Parse(time.RFC3339Nano, m.ExpiresAt)
		if e != nil || !expiry.Equal(start.Add(time.Duration(tc.days)*24*time.Hour)) {
			t.Fatal("wrong retention")
		}
	}
}
