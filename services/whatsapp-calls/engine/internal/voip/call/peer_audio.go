package call

import (
	"slices"
	"sync"
	"time"

	"wacalls/internal/voip/media"
	"wacalls/internal/voip/media/mlow"
)

const (
	peerFrameDuration = 60 * time.Millisecond
	peerPlayoutDelay  = 3 * peerFrameDuration
	peerMaxHold       = 4 * peerFrameDuration
	peerPlayoutTick   = 5 * time.Millisecond
	peerQueueLimit    = 8
	peerClockRate     = 16000
)

// PeerAudioStatistics contains aggregate RTP observations only. Rejections
// include duplicate packets rejected by SRTP replay protection.
type PeerAudioStatistics struct {
	Frames           uint64 `json:"frames"`
	SilenceFrames    uint64 `json:"silence_frames"`
	RejectedPackets  uint64 `json:"srtp_rejected_packets"`
	ArrivalGaps      uint64 `json:"gaps_over_200ms"`
	MaxArrivalGapMS  int64  `json:"max_gap_ms"`
	LastArrivalGapMS int64  `json:"last_gap_at_ms"`
	MissingSequences uint64 `json:"missing_sequences"`
	ReorderedFrames  uint64 `json:"reordered_frames"`
	LateFrames       uint64 `json:"late_frames"`
	DroppedFrames    uint64 `json:"buffer_dropped_frames"`
	MaxHoldMS        int64  `json:"max_playout_hold_ms"`
}

type receivedAudio struct {
	packet *media.RtpPacket
	at     time.Time
}

// The receiver sorts authenticated packets before advancing the stateful
// decoder. RTP time preserves pauses at the sender, including sparse silence
// packets. A bounded hold prevents a stalled or discontinuous stream from
// building up latency. No synthetic silence is added to the recording.
type peerAudioBuffer struct {
	mu sync.Mutex

	queue       []receivedAudio
	initialAt   time.Time
	lastArrival time.Time
	streamAt    time.Time
	streamSSRC  uint32
	playedAt    time.Time
	playedSeq   uint16
	playedTS    uint32
	stats       PeerAudioStatistics
}

func (b *peerAudioBuffer) reject() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.stats.RejectedPackets++
}

func (b *peerAudioBuffer) snapshot() PeerAudioStatistics {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.stats
}

func (b *peerAudioBuffer) clearQueue() {
	b.queue = nil
	b.initialAt = time.Time{}
	b.playedAt = time.Time{}
	b.streamAt = time.Time{}
}

func (b *peerAudioBuffer) stop() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.clearQueue()
}

func (b *peerAudioBuffer) push(packet *media.RtpPacket, now time.Time) {
	b.mu.Lock()
	defer b.mu.Unlock()

	toc := mlow.ParseSmplTOC(packet.Payload[0])
	if !toc.StdOpus && (toc.SID || !toc.Active) {
		b.stats.SilenceFrames++
	}
	b.stats.Frames++
	if !now.Before(b.lastArrival) {
		if !b.lastArrival.IsZero() {
			gap := now.Sub(b.lastArrival).Milliseconds()
			if gap > 200 {
				b.stats.ArrivalGaps++
				b.stats.MaxArrivalGapMS = max(b.stats.MaxArrivalGapMS, gap)
				b.stats.LastArrivalGapMS = now.UnixMilli()
			}
		}
		b.lastArrival = now
	}

	header := packet.Header
	if !b.streamAt.IsZero() && header.Ssrc != b.streamSSRC {
		if now.Sub(b.streamAt) < peerMaxHold {
			b.stats.DroppedFrames++
			return
		}
		b.stats.DroppedFrames += uint64(len(b.queue))
		b.clearQueue()
	}
	b.streamSSRC = header.Ssrc
	b.streamAt = maxTime(b.streamAt, now)
	if !b.playedAt.IsZero() && int16(header.SequenceNumber-b.playedSeq) <= 0 {
		b.stats.LateFrames++
		return
	}

	insert := len(b.queue)
	for i, frame := range b.queue {
		delta := int16(header.SequenceNumber - frame.packet.Header.SequenceNumber)
		if delta == 0 {
			return
		}
		if delta < 0 {
			insert = i
			b.stats.ReorderedFrames++
			break
		}
	}
	if len(b.queue) == peerQueueLimit {
		b.stats.DroppedFrames++
		if insert == peerQueueLimit {
			return
		}
		b.queue = slices.Delete(b.queue, peerQueueLimit-1, peerQueueLimit)
	}
	b.queue = slices.Insert(b.queue, insert, receivedAudio{packet: packet, at: now})
	if b.initialAt.IsZero() {
		b.initialAt = now
	}
}

func (b *peerAudioBuffer) pop(now time.Time) *media.RtpPacket {
	b.mu.Lock()
	defer b.mu.Unlock()
	for len(b.queue) > 0 && now.Sub(b.queue[0].at) > peerMaxHold {
		b.stats.DroppedFrames++
		b.queue = slices.Delete(b.queue, 0, 1)
	}
	if len(b.queue) == 0 {
		b.initialAt = time.Time{}
		return nil
	}

	frame := b.queue[0]
	header := frame.packet.Header
	due := b.initialAt.Add(peerPlayoutDelay)
	if !b.playedAt.IsZero() {
		advance := time.Duration(header.Timestamp-b.playedTS) * time.Second / peerClockRate
		due = b.playedAt.Add(max(peerFrameDuration, advance))
		if due.Before(frame.at) || due.After(frame.at.Add(peerMaxHold)) {
			// Rebuild runway after a long pause or timestamp discontinuity.
			due = maxTime(frame.at.Add(peerPlayoutDelay), b.playedAt.Add(peerFrameDuration))
		}
	}
	if now.Before(due) {
		return nil
	}
	if !b.playedAt.IsZero() {
		b.stats.MissingSequences += uint64(uint16(header.SequenceNumber-b.playedSeq) - 1)
	}
	b.playedAt = due
	b.playedSeq = header.SequenceNumber
	b.playedTS = header.Timestamp
	b.stats.MaxHoldMS = max(b.stats.MaxHoldMS, now.Sub(frame.at).Milliseconds())
	b.queue = slices.Delete(b.queue, 0, 1)
	return frame.packet
}

func maxTime(a, b time.Time) time.Time {
	if a.After(b) {
		return a
	}
	return b
}

func (m *CallManager) PeerAudioStats() PeerAudioStatistics {
	return m.peerAudio.snapshot()
}

func (m *CallManager) playPeerAudio(stop <-chan struct{}) {
	ticker := time.NewTicker(peerPlayoutTick)
	defer ticker.Stop()
	for {
		select {
		case <-stop:
			return
		case <-ticker.C:
			m.mu.Lock()
			if m.peerPlayoutStop != stop || m.codec == nil {
				m.mu.Unlock()
				return
			}
			packet := m.peerAudio.pop(time.Now())
			if packet == nil {
				m.mu.Unlock()
				continue
			}
			pcm, err := m.codec.Decode(packet.Payload)
			if err == nil {
				pcm = media.NormalizeFrame(pcm, m.codec.FrameSize())
			}
			m.mu.Unlock()
			if err == nil && m.OnPeerAudio != nil {
				m.OnPeerAudio(pcm)
			}
		}
	}
}
