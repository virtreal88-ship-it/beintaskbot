package main

import (
	"fmt"
	"log/slog"
	"sync/atomic"
	"time"

	"wacalls/internal/voip/media"

	"github.com/pion/webrtc/v4"
)

// pcmChannelLabel is the data channel the browser opens to carry raw 16 kHz mono
// Int16 LE PCM in both directions. The browser side must create it with this label.
const pcmChannelLabel = "pcm"

// Bridge is the browser-leg adapter: it carries raw PCM between the browser and
// the CallManager over a WebRTC data channel. The call core only ever sees
// []float32 PCM, so it stays unaware of the transport (no Opus here anymore).
type Bridge struct {
	stream *streamBridge
	pc     *webrtc.PeerConnection
	dc     atomic.Pointer[webrtc.DataChannel]
	log    *slog.Logger

	// OnBrowserPCM is invoked with decoded 16 kHz mono PCM captured from the browser mic.
	OnBrowserPCM func(pcm []float32)
	// OnTerminalICE fires when the peer connection fails or closes.
	OnTerminalICE func()
}

func NewBridge(offerSDP string, log *slog.Logger, onPCM func([]float32), onTerminal func()) (*Bridge, string, error) {
	settings := webrtc.SettingEngine{}
	if err := settings.SetEphemeralUDPPortRange(40000, 40100); err != nil {
		return nil, "", fmt.Errorf("browser_port_configuration")
	}
	pc, err := webrtc.NewAPI(webrtc.WithSettingEngine(settings)).NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		return nil, "", err
	}
	br := &Bridge{pc: pc, log: log, OnBrowserPCM: onPCM, OnTerminalICE: onTerminal}

	pc.OnDataChannel(func(dc *webrtc.DataChannel) {
		if dc.Label() != pcmChannelLabel || !br.dc.CompareAndSwap(nil, dc) {
			_ = dc.Close()
			return
		}
		dc.OnMessage(func(msg webrtc.DataChannelMessage) {
			if cb := br.OnBrowserPCM; cb != nil && !msg.IsString && len(msg.Data) > 0 && len(msg.Data) <= 6400 && len(msg.Data)%2 == 0 {
				cb(media.PCMInt16LEToFloat32(msg.Data))
			}
		})
	})

	pc.OnICEConnectionStateChange(func(s webrtc.ICEConnectionState) {
		log.Debug("browser ice state", "state", s.String())
		if s == webrtc.ICEConnectionStateFailed || s == webrtc.ICEConnectionStateClosed {
			if br.OnTerminalICE != nil {
				br.OnTerminalICE()
			}
		}
	})

	if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: offerSDP}); err != nil {
		pc.Close()
		return nil, "", err
	}
	answer, err := pc.CreateAnswer(nil)
	if err != nil {
		pc.Close()
		return nil, "", err
	}
	gatherComplete := webrtc.GatheringCompletePromise(pc)
	if err := pc.SetLocalDescription(answer); err != nil {
		pc.Close()
		return nil, "", err
	}
	select {
	case <-gatherComplete:
	case <-time.After(10 * time.Second):
		_ = pc.Close()
		return nil, "", fmt.Errorf("browser_ice_timeout")
	}

	return br, pc.LocalDescription().SDP, nil
}

// WritePCM sends 16 kHz mono float32 PCM to the browser as Int16 LE over the data
// channel. It is a no-op until the channel is open.
func (b *Bridge) WritePCM(pcm []float32) error {
	if b.stream != nil {
		return b.stream.write(pcm)
	}
	dc := b.dc.Load()
	if dc == nil || len(pcm) == 0 || dc.ReadyState() != webrtc.DataChannelStateOpen {
		return nil
	}
	if dc.BufferedAmount() > 128<<10 {
		return fmt.Errorf("browser_audio_backpressure")
	}
	return dc.Send(media.PCMFloat32ToInt16LE(pcm))
}

func (b *Bridge) Close() {
	if b.stream != nil {
		b.stream.close()
	}
	if b.pc != nil {
		_ = b.pc.Close()
	}
}
