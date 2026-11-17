package main

// PCM travels only through an authenticated Worker relay, never an open audio
// broadcast endpoint. One browser attachment is permitted for each call.
import (
	"context"
	"errors"
	"github.com/coder/websocket"
	"net/http"
	"sync"
	"sync/atomic"
	"time"
	"wacalls/internal/voip/media"
)

type streamBridge struct {
	output         chan []byte
	done           chan struct{}
	once           sync.Once
	attached       atomic.Bool
	onPCM          func([]float32)
	onEnd          func()
	startMu        sync.Mutex
	startOnce      sync.Once
	startRequested atomic.Bool
	onStart        func() error
}

func newStreamBridge(onPCM func([]float32), onEnd func()) *Bridge {
	s := &streamBridge{output: make(chan []byte, 64), done: make(chan struct{}), onPCM: onPCM, onEnd: onEnd}
	return &Bridge{stream: s}
}
func (s *streamBridge) close() { s.once.Do(func() { close(s.done) }) }
func (s *streamBridge) setStart(start func() error) {
	s.startMu.Lock()
	s.onStart = start
	s.startMu.Unlock()
}
func (s *streamBridge) startProvider() {
	s.startOnce.Do(func() {
		select {
		case <-s.done:
			return
		default:
		}
		s.startMu.Lock()
		start := s.onStart
		s.startMu.Unlock()
		if start != nil {
			if err := start(); err != nil {
				s.close()
			}
		}
	})
}
func (s *streamBridge) write(pcm []float32) error {
	select {
	case <-s.done:
		return errors.New("audio_closed")
	default:
	}
	raw := media.PCMFloat32ToInt16LE(pcm)
	if len(raw) == 0 || len(raw) > 6400 {
		return errors.New("audio_frame_invalid")
	}
	select {
	case <-s.done:
		return errors.New("audio_closed")
	case s.output <- raw:
		return nil
	default:
		s.close()
		return errors.New("audio_backpressure")
	}
}
func (s *streamBridge) serve(w http.ResponseWriter, r *http.Request) {
	select {
	case <-s.done:
		http.Error(w, "audio_closed", 409)
		return
	default:
	}
	if !s.attached.CompareAndSwap(false, true) {
		http.Error(w, "audio_already_attached", 409)
		return
	}
	c, e := websocket.Accept(w, r, &websocket.AcceptOptions{CompressionMode: websocket.CompressionDisabled})
	if e != nil {
		s.close()
		if s.onEnd != nil {
			s.onEnd()
		}
		return
	}
	defer c.CloseNow()
	defer s.close()
	defer func() {
		if s.onEnd != nil {
			s.onEnd()
		}
	}()
	c.SetReadLimit(6400)
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	go func() {
		defer cancel()
		for {
			select {
			case <-s.done:
				return
			case <-ctx.Done():
				return
			case data := <-s.output:
				writeCtx, stop := context.WithTimeout(ctx, 2*time.Second)
				e := c.Write(writeCtx, websocket.MessageBinary, data)
				stop()
				if e != nil {
					return
				}
			}
		}
	}()
	window := time.Now()
	bytes := 0
	for {
		kind, data, e := c.Read(ctx)
		if e != nil {
			return
		}
		if kind != websocket.MessageBinary || len(data) == 0 || len(data)%2 != 0 {
			return
		}
		if time.Since(window) >= time.Second {
			window = time.Now()
			bytes = 0
		}
		bytes += len(data)
		if bytes > 65536 {
			return
		}
		if s.onPCM != nil {
			// The first browser PCM (including muted silence) proves the whole
			// authenticated relay is open. A private upstream handshake does not.
			if s.startRequested.CompareAndSwap(false, true) {
				go s.startProvider()
			}
			s.onPCM(media.PCMInt16LEToFloat32(data))
		} else {
			if s.startRequested.CompareAndSwap(false, true) {
				go s.startProvider()
			}
		}
	}
}

func (s *server) handleStream(w http.ResponseWriter, r *http.Request) {
	sess, ok := s.sessions.Get(r.PathValue("sid"))
	if !ok {
		http.Error(w, "source_unavailable", 404)
		return
	}
	id := r.PathValue("id")
	rec, ok := s.broker.getCall(id)
	if !ok || rec.SessionID != sess.id || rec.Owner == nil || *rec.Owner != "app:"+r.PathValue("applicationCallID") {
		http.Error(w, "call_unavailable", 403)
		return
	}
	ac, ok := sess.reg.get(id)
	if !ok {
		http.Error(w, "call_closed", 409)
		return
	}
	bridge := ac.bridge.Load()
	if bridge == nil || bridge.stream == nil {
		http.Error(w, "audio_unavailable", 409)
		return
	}
	bridge.stream.serve(w, r)
}
