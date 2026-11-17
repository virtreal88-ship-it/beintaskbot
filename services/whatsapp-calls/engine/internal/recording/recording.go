// Package recording records only explicitly consented test calls. Audio and
// identities are never logged. Each channel has a common monotonic time origin.
package recording

import (
	"archive/zip"
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"sync"
	"time"
)

const Rate = 16000
const MaxSeconds = 120
const MaxSamples = Rate * MaxSeconds

type Manifest struct {
	Version    int      `json:"version"`
	SessionID  string   `json:"session_id"`
	CallID     string   `json:"call_id"`
	AccountJID string   `json:"account_jid"`
	PeerJID    string   `json:"peer_jid"`
	StartedAt  string   `json:"started_at"`
	EndedAt    string   `json:"ended_at"`
	ExpiresAt  string   `json:"expires_at"`
	DurationMS int      `json:"duration_ms"`
	SampleRate int      `json:"sample_rate"`
	Channels   []string `json:"channels"`
	Samples    [2]int   `json:"samples_received"`
	NonSilent  [2]int   `json:"non_silent_samples"`
	Gaps       [2]int   `json:"gaps"`
	Complete   bool     `json:"complete"`
	EndCode    string   `json:"end_code"`
	SHA256     string   `json:"sha256"`
	Consent    bool     `json:"consent_confirmed"`
}

type Capture struct {
	mu        sync.Mutex
	start     time.Time
	tracks    [2][]int16
	cursor    [2]int
	manifest  Manifest
	closed    bool
	truncated bool
}

func New(session, call, account, peer string, start time.Time) *Capture {
	return newCapture(session, call, account, peer, start, 24*time.Hour)
}

// NewManaged keeps the signed business recording for the owner-approved 30 days.
// The engine's local delivery spool still expires after 24 hours; Worker owns
// the durable recording and its retention, independently of pairing/lab expiry.
func NewManaged(session, call, account, peer string, start time.Time) *Capture {
	return newCapture(session, call, account, peer, start, 30*24*time.Hour)
}

func newCapture(session, call, account, peer string, start time.Time, retention time.Duration) *Capture {
	return &Capture{start: start, manifest: Manifest{Version: 1, SessionID: session, CallID: call,
		AccountJID: account, PeerJID: peer, StartedAt: start.UTC().Format(time.RFC3339Nano),
		ExpiresAt:  start.Add(retention).UTC().Format(time.RFC3339Nano),
		SampleRate: Rate, Channels: []string{"employee", "caller"}, Consent: true}}
}

// Add copies bounded PCM. Interarrival gaps are kept as silence; the manifest
// exposes discontinuities rather than asserting all network audio was captured.
func (c *Capture) Add(side int, pcm []float32, now time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed || side < 0 || side > 1 || len(pcm) == 0 || len(pcm) > 3200 {
		return
	}
	position := int(now.Sub(c.start).Seconds()*Rate) - len(pcm)
	if position < 0 {
		position = 0
	}
	if c.cursor[side] > 0 && position < c.cursor[side]+Rate/5 {
		position = c.cursor[side]
	}
	if position < c.cursor[side] {
		position = c.cursor[side]
	}
	if position > c.cursor[side]+Rate/5 && c.cursor[side] > 0 {
		c.manifest.Gaps[side]++
	}
	if position >= MaxSamples {
		c.truncated = true
		return
	}
	count := min(len(pcm), MaxSamples-position)
	if count < len(pcm) {
		c.truncated = true
	}
	end := position + count
	if end > len(c.tracks[side]) {
		c.tracks[side] = append(c.tracks[side], make([]int16, end-len(c.tracks[side]))...)
	}
	for i, v := range pcm[:count] {
		if math.IsNaN(float64(v)) || math.IsInf(float64(v), 0) {
			v = 0
		}
		v = max(float32(-1), min(float32(1), v))
		if v > 0.001 || v < -0.001 {
			c.manifest.NonSilent[side]++
		}
		c.tracks[side][position+i] = int16(v * 32767)
	}
	c.cursor[side] = end
	c.manifest.Samples[side] += count
}

func (c *Capture) Finish(now time.Time, graceful bool, seed []byte) ([]byte, Manifest, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return nil, Manifest{}, errors.New("recording_already_closed")
	}
	c.closed = true
	defer func() { c.tracks = [2][]int16{} }()
	if len(seed) != ed25519.SeedSize {
		return nil, Manifest{}, errors.New("recording_key_invalid")
	}
	frames := min(MaxSamples, max(len(c.tracks[0]), len(c.tracks[1]), int(now.Sub(c.start).Seconds()*Rate)))
	if frames < 1 {
		return nil, Manifest{}, errors.New("recording_empty")
	}
	wav := make([]byte, 44+frames*4)
	copy(wav, "RIFF")
	binary.LittleEndian.PutUint32(wav[4:], uint32(len(wav)-8))
	copy(wav[8:], "WAVEfmt ")
	binary.LittleEndian.PutUint32(wav[16:], 16)
	binary.LittleEndian.PutUint16(wav[20:], 1)
	binary.LittleEndian.PutUint16(wav[22:], 2)
	binary.LittleEndian.PutUint32(wav[24:], Rate)
	binary.LittleEndian.PutUint32(wav[28:], Rate*4)
	binary.LittleEndian.PutUint16(wav[32:], 4)
	binary.LittleEndian.PutUint16(wav[34:], 16)
	copy(wav[36:], "data")
	binary.LittleEndian.PutUint32(wav[40:], uint32(frames*4))
	for side := 0; side < 2; side++ {
		for i, v := range c.tracks[side] {
			if i < frames {
				binary.LittleEndian.PutUint16(wav[44+i*4+side*2:], uint16(v))
			}
		}
	}
	m := c.manifest
	m.EndedAt = now.UTC().Format(time.RFC3339Nano)
	m.DurationMS = frames * 1000 / Rate
	m.Complete = graceful && !c.truncated && m.Samples[0] > 0 && m.Samples[1] > 0 && m.Gaps[0] == 0 && m.Gaps[1] == 0
	m.EndCode = "interrupted"
	if m.Complete {
		m.EndCode = "normal"
	} else if c.truncated {
		m.EndCode = "duration_limit"
	} else if m.Samples[0] == 0 || m.Samples[1] == 0 {
		m.EndCode = "missing_direction"
	} else if m.Gaps[0] > 0 || m.Gaps[1] > 0 {
		m.EndCode = "audio_gaps"
	}
	digest := sha256.Sum256(wav)
	m.SHA256 = hex.EncodeToString(digest[:])
	manifest, err := json.Marshal(m)
	if err != nil {
		return nil, m, err
	}
	sig := ed25519.Sign(ed25519.NewKeyFromSeed(seed), manifest)
	var out bytes.Buffer
	z := zip.NewWriter(&out)
	for _, part := range []struct {
		name string
		data []byte
	}{{"manifest.json", manifest}, {"signature.ed25519", sig}, {"recording.wav", wav}} {
		w, e := z.CreateHeader(&zip.FileHeader{Name: part.name, Method: zip.Store})
		if e != nil {
			return nil, m, e
		}
		if _, e = w.Write(part.data); e != nil {
			return nil, m, e
		}
	}
	if err = z.Close(); err != nil {
		return nil, m, err
	}
	return out.Bytes(), m, nil
}
