package call

import (
	"math"
	"testing"
	"time"

	"wacalls/internal/voip/core"
	"wacalls/internal/voip/media"
)

func audioPacket(seq uint16, ts, ssrc uint32) *media.RtpPacket {
	return &media.RtpPacket{
		Header:  media.NewRtpHeader(core.PayloadTypeWhatsAppOpus, seq, ts, ssrc),
		Payload: []byte{0x50},
	}
}

func expectPlayout(t *testing.T, buffer *peerAudioBuffer, now time.Time, seq uint16) {
	t.Helper()
	packet := buffer.pop(now)
	if packet == nil || packet.Header.SequenceNumber != seq {
		t.Fatalf("at %v wanted RTP sequence %d, got %+v", now, seq, packet)
	}
}

func TestPeerAudioSmoothsDelayedBurstInRTPOrder(t *testing.T) {
	var buffer peerAudioBuffer
	now := time.Now()
	firstTS := uint32(math.MaxUint32 - 1400)
	buffer.push(audioPacket(65534, firstTS, 123), now)
	expectPlayout(t, &buffer, now.Add(180*time.Millisecond), 65534)

	// All samples arrive, after a 212 ms gap in delivery. The middle two RTP
	// packets arrive out of order across wrap.
	buffer.push(audioPacket(0, firstTS+1920, 123), now.Add(212*time.Millisecond))
	buffer.push(audioPacket(65535, firstTS+960, 123), now.Add(216*time.Millisecond))
	buffer.push(audioPacket(1, firstTS+2880, 123), now.Add(222*time.Millisecond))
	if packet := buffer.pop(now.Add(239 * time.Millisecond)); packet != nil {
		t.Fatal("delayed frames played in a burst")
	}
	for step, seq := range []uint16{65535, 0, 1} {
		expectPlayout(t, &buffer, now.Add(time.Duration(240+step*60)*time.Millisecond), seq)
	}
	got := buffer.snapshot()
	if got.Frames != 4 || got.ArrivalGaps != 1 || got.MaxArrivalGapMS != 212 ||
		got.ReorderedFrames != 1 || got.MissingSequences != 0 ||
		got.DroppedFrames != 0 || got.MaxHoldMS > 240 {
		t.Fatalf("unexpected RTP evidence: %+v", got)
	}
}

func TestPeerAudioPreservesSilenceAndCountsActualSequenceGap(t *testing.T) {
	var buffer peerAudioBuffer
	now := time.Now()
	buffer.push(audioPacket(50, 1000, 123), now)
	expectPlayout(t, &buffer, now.Add(180*time.Millisecond), 50)

	// A gap in RTP time without a skipped sequence is sparse audio, not a
	// missing packet. This SID frame represents 60 ms of decoded silence.
	sid := audioPacket(51, 1000+3840, 123)
	sid.Payload = []byte{0x90}
	buffer.push(sid, now.Add(235*time.Millisecond))
	buffer.push(audioPacket(53, 1000+5760, 123), now.Add(360*time.Millisecond))
	if packet := buffer.pop(now.Add(410 * time.Millisecond)); packet != nil {
		t.Fatal("sender silence was compressed")
	}
	expectPlayout(t, &buffer, now.Add(420*time.Millisecond), 51)

	expectPlayout(t, &buffer, now.Add(540*time.Millisecond), 53)
	buffer.push(audioPacket(52, 1000+4800, 123), now.Add(570*time.Millisecond))
	got := buffer.snapshot()
	if got.MissingSequences != 1 || got.SilenceFrames != 1 || got.LateFrames != 1 {
		t.Fatalf("failed to distinguish silence, packet loss and late arrivals: %+v", got)
	}
}

func TestPeerAudioDropsStaleBacklogAndResumes(t *testing.T) {
	var buffer peerAudioBuffer
	now := time.Now()
	for i := 0; i < 50; i++ {
		buffer.push(audioPacket(uint16(i), uint32(960*i), 123), now)
	}
	if len(buffer.queue) != peerQueueLimit {
		t.Fatal("audio receive buffer did not stay bounded")
	}
	if packet := buffer.pop(now.Add(time.Second)); packet != nil {
		t.Fatal("audio played from an expired backlog")
	}
	buffer.push(audioPacket(50, 48000, 123), now.Add(time.Second))
	expectPlayout(t, &buffer, now.Add(time.Second+peerPlayoutDelay), 50)
	if got := buffer.snapshot(); got.DroppedFrames != 50 || got.MaxHoldMS != 180 {
		t.Fatalf("unexpected backlog evidence: %+v", got)
	}
}

func TestPeerAudioRelayAuthenticatesBeforeBufferedDecode(t *testing.T) {
	manager := NewCallManager(nil, nil)
	defer manager.cleanupMedia()
	manager.selfSsrc = 321
	manager.actualPeerSet = true
	manager.initCodec()
	key := core.SrtpKeyingMaterial{MasterKey: make([]byte, 16), MasterSalt: make([]byte, 14)}
	rcv, err := media.NewSrtpSession(key, key, 4, 4)
	if err != nil {
		t.Fatal(err)
	}
	manager.srtpSession = rcv
	sender, err := media.NewSrtpSession(key, key, 4, 4)
	if err != nil {
		t.Fatal(err)
	}
	codec, err := media.NewMLowCodec(media.DefaultCodecOptions)
	if err != nil {
		t.Fatal(err)
	}
	defer codec.Close()
	input := make([]float32, codec.FrameSize())
	for i := range input {
		input[i] = float32(math.Sin(2 * math.Pi * 220 * float64(i) / 16000))
	}
	packet := audioPacket(100, 1000, 123)
	packet.Payload, err = codec.Encode(input)
	if err != nil {
		t.Fatal(err)
	}
	wire, err := sender.Protect(packet)
	if err != nil {
		t.Fatal(err)
	}
	played := make(chan []float32, 2)
	manager.OnPeerAudio = func(pcm []float32) { played <- pcm }
	badTag := append([]byte(nil), wire...)
	badTag[len(badTag)-1] ^= 1
	manager.onRelayData(badTag)
	manager.onRelayData(wire)
	manager.onRelayData(wire) // Replay protection still applies before the buffer.
	select {
	case pcm := <-played:
		if len(pcm) != codec.FrameSize() {
			t.Fatalf("unexpected decoded audio length: %d", len(pcm))
		}
		var energy float32
		for _, sample := range pcm {
			energy += sample * sample
		}
		if energy == 0 {
			t.Fatal("authenticated speech was replaced with silence")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("authenticated RTP was never played")
	}
	got := manager.PeerAudioStats()
	if got.Frames != 1 || got.RejectedPackets != 2 || got.MaxHoldMS < 180 || got.MaxHoldMS > 240 {
		t.Fatalf("unexpected decoder evidence: %+v", got)
	}
}
