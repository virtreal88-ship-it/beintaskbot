package recording

import (
	"archive/zip"
	"bytes"
	"crypto/ed25519"
	"encoding/binary"
	"io"
	"sync"
	"testing"
	"time"
)

func TestSignedStereoKeepsBothSidesAndTiming(t *testing.T) {
	now := time.Date(2026, 11, 17, 12, 0, 0, 0, time.UTC)
	c := New("session", "call", "self@s.whatsapp.net", "peer@lid", now)
	left := make([]float32, 320)
	right := make([]float32, 320)
	for i := range left {
		left[i] = 0.25
		right[i] = -0.5
	}
	c.Add(0, left, now.Add(20*time.Millisecond))
	c.Add(1, right, now.Add(40*time.Millisecond))
	seed := make([]byte, 32)
	data, m, e := c.Finish(now.Add(60*time.Millisecond), true, seed)
	if e != nil || !m.Complete || m.DurationMS != 60 || m.Samples != [2]int{320, 320} {
		t.Fatalf("unexpected recording metadata: %v", e)
	}
	z, e := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if e != nil {
		t.Fatal(e)
	}
	files := map[string][]byte{}
	for _, f := range z.File {
		r, _ := f.Open()
		files[f.Name], _ = io.ReadAll(r)
		r.Close()
	}
	if !ed25519.Verify(ed25519.NewKeyFromSeed(seed).Public().(ed25519.PublicKey), files["manifest.json"], files["signature.ed25519"]) {
		t.Fatal("invalid signature")
	}
	wav := files["recording.wav"]
	if binary.LittleEndian.Uint16(wav[22:]) != 2 || binary.LittleEndian.Uint32(wav[24:]) != Rate {
		t.Fatal("wrong format")
	}
	if int16(binary.LittleEndian.Uint16(wav[44:])) <= 0 || binary.LittleEndian.Uint16(wav[46:]) != 0 {
		t.Fatal("employee audio not on left timeline")
	}
	if int16(binary.LittleEndian.Uint16(wav[44+320*4+2:])) >= 0 {
		t.Fatal("caller audio missing or wrong timeline")
	}
	if _, _, e = c.Finish(now, true, seed); e == nil {
		t.Fatal("duplicate finish accepted")
	}
}

func TestMissingSideInterruptionAndDurationAreNotComplete(t *testing.T) {
	for _, kind := range []string{"missing", "interrupted", "limit", "gap"} {
		t.Run(kind, func(t *testing.T) {
			now := time.Now()
			c := New("s", "c", "a", "p", now)
			c.Add(0, []float32{0.2}, now)
			if kind != "missing" {
				c.Add(1, []float32{0.2}, now)
			}
			if kind == "limit" {
				c.Add(0, []float32{0.2}, now.Add(121*time.Second))
			}
			if kind == "gap" {
				c.Add(0, []float32{0.2}, now.Add(time.Second))
			}
			_, m, e := c.Finish(now.Add(time.Second), kind != "interrupted", make([]byte, 32))
			if e != nil || m.Complete {
				t.Fatal("incomplete capture presented as complete")
			}
		})
	}
}

func TestConcurrentCloseRejectsLateAudio(t *testing.T) {
	now := time.Now()
	c := New("s", "c", "a", "p", now)
	var wg sync.WaitGroup
	for side := 0; side < 2; side++ {
		wg.Add(1)
		go func(side int) {
			defer wg.Done()
			for i := 0; i < 1000; i++ {
				c.Add(side, []float32{.2, .3}, time.Now())
			}
		}(side)
	}
	_, _, _ = c.Finish(now.Add(time.Second), false, make([]byte, 32))
	wg.Wait()
	if len(c.tracks[0])+len(c.tracks[1]) != 0 {
		t.Fatal("late audio retained after close")
	}
}
