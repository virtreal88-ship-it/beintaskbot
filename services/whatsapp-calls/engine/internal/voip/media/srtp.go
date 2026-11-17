package media

import (
	"crypto/aes"
	"crypto/cipher"
	"fmt"
	"sync"

	"github.com/pion/srtp/v3"
	"wacalls/internal/voip/core"
)

type SrtpErrorType string

const (
	SrtpErrPacketTooShort SrtpErrorType = "packet_too_short"
	SrtpErrAuthFailed     SrtpErrorType = "auth_failed"
	SrtpErrEncryption     SrtpErrorType = "encryption"
	SrtpErrDecryption     SrtpErrorType = "decryption"
)

type SrtpError struct {
	Type SrtpErrorType
	Msg  string
}

func (e *SrtpError) Error() string { return fmt.Sprintf("srtp %s: %s", e.Type, e.Msg) }

// Each context is one-directional. Pion authenticates before updating receive
// state and keeps rollover/replay state separately for each SSRC. The mutex is
// required because media callbacks may run concurrently.
type SrtpContext struct {
	mu        sync.Mutex
	ctx       *srtp.Context
	direction string
}

func NewSrtpContext(keying core.SrtpKeyingMaterial, authTagLen int) (*SrtpContext, error) {
	// Validate ourselves: upstream errors must never format key bytes.
	if len(keying.MasterKey) != 16 || len(keying.MasterSalt) != 14 {
		return nil, fmt.Errorf("invalid SRTP key material dimensions")
	}
	if authTagLen == 0 {
		authTagLen = core.SRTPAuthTagLen
	}
	var profile srtp.ProtectionProfile
	switch authTagLen {
	case 4:
		profile = srtp.ProtectionProfileAes128CmHmacSha1_32
	case 10:
		profile = srtp.ProtectionProfileAes128CmHmacSha1_80
	default:
		return nil, fmt.Errorf("unsupported SRTP authentication tag length")
	}
	ctx, err := srtp.CreateContext(keying.MasterKey, keying.MasterSalt, profile, srtp.SRTPReplayProtection(128))
	if err != nil {
		return nil, fmt.Errorf("SRTP context initialization failed")
	}
	return &SrtpContext{ctx: ctx}, nil
}
func (c *SrtpContext) Protect(packet *RtpPacket) ([]byte, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.direction == "receive" {
		return nil, fmt.Errorf("SRTP context direction mismatch")
	}
	c.direction = "send"
	if packet == nil || packet.Header == nil {
		return nil, fmt.Errorf("missing RTP packet")
	}
	data, err := packet.Encode()
	if err != nil {
		return nil, &SrtpError{SrtpErrEncryption, "invalid RTP packet"}
	}
	out, err := c.ctx.EncryptRTP(nil, data, nil)
	if err != nil {
		return nil, &SrtpError{SrtpErrEncryption, "packet encryption rejected"}
	}
	return out, nil
}
func (c *SrtpContext) Unprotect(data []byte) (*RtpPacket, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.direction == "send" {
		return nil, fmt.Errorf("SRTP context direction mismatch")
	}
	c.direction = "receive"
	plain, err := c.ctx.DecryptRTP(nil, data, nil)
	// Return an allowlisted code, never packet contents or cryptographic material.
	if err != nil {
		return nil, &SrtpError{SrtpErrAuthFailed, "packet rejected"}
	}
	pkt, err := DecodeRtpPacket(plain)
	if err != nil {
		return nil, &SrtpError{SrtpErrDecryption, "invalid RTP packet"}
	}
	return pkt, nil
}

type SrtpSession struct{ sendCtx, recvCtx *SrtpContext }

func NewSrtpSession(sendKey, recvKey core.SrtpKeyingMaterial, sendAuthLen, recvAuthLen int) (*SrtpSession, error) {
	sc, err := NewSrtpContext(sendKey, sendAuthLen)
	if err != nil {
		return nil, err
	}
	rc, err := NewSrtpContext(recvKey, recvAuthLen)
	if err != nil {
		return nil, err
	}
	return &SrtpSession{sendCtx: sc, recvCtx: rc}, nil
}
func (s *SrtpSession) Protect(packet *RtpPacket) ([]byte, error) { return s.sendCtx.Protect(packet) }
func (s *SrtpSession) Unprotect(data []byte) (*RtpPacket, error) { return s.recvCtx.Unprotect(data) }

func deriveSrtpKey(masterKey, masterSalt []byte, label byte, length int) ([]byte, error) {
	if len(masterKey) != 16 || len(masterSalt) != 14 || length <= 0 {
		return nil, fmt.Errorf("invalid SRTP key material dimensions")
	}
	iv := make([]byte, 16)
	copy(iv, masterSalt[:14])
	iv[7] ^= label

	out := make([]byte, length)
	if err := aesCtrXor(masterKey, iv, make([]byte, length), out); err != nil {
		return nil, err
	}
	return out, nil
}

func aesCtrXor(key, iv, src, dst []byte) error {
	block, err := aes.NewCipher(key)
	if err != nil {
		return err
	}
	cipher.NewCTR(block, iv).XORKeyStream(dst, src)
	return nil
}
