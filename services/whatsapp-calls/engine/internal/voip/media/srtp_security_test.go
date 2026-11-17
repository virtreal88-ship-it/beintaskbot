package media

import (
	"bytes"
	"testing"
	"wacalls/internal/voip/core"
)

func testCrypto(t *testing.T) (*SrtpContext, *SrtpContext) {
	t.Helper()
	km := core.SrtpKeyingMaterial{MasterKey: bytes.Repeat([]byte{0x11}, 16), MasterSalt: bytes.Repeat([]byte{0x22}, 14)}
	a, e := NewSrtpContext(km, 4)
	if e != nil {
		t.Fatal(e)
	}
	b, e := NewSrtpContext(km, 4)
	if e != nil {
		t.Fatal(e)
	}
	return a, b
}
func sealed(t *testing.T, c *SrtpContext, seq uint16, ssrc uint32) []byte {
	t.Helper()
	b, e := c.Protect(&RtpPacket{Header: NewRtpHeader(111, seq, uint32(seq)*320, ssrc), Payload: []byte{1, 2, 3, 4}})
	if e != nil {
		t.Fatal(e)
	}
	return b
}
func TestSrtpRejectsTamperingWithoutConsumingPacket(t *testing.T) {
	for _, offset := range []int{1, 3, 7, 11, 12, 16} {
		a, b := testCrypto(t)
		wire := sealed(t, a, 500, 17)
		changed := append([]byte(nil), wire...)
		changed[offset] ^= 1
		if _, e := b.Unprotect(changed); e == nil {
			t.Fatalf("tamper accepted at %d", offset)
		}
		if _, e := b.Unprotect(wire); e != nil {
			t.Fatalf("valid packet rejected after forgery: %v", e)
		}
		if _, e := b.Unprotect(wire); e == nil {
			t.Fatal("replay accepted")
		}
	}
}
func TestSrtpRolloverReorderAndSeparateSSRC(t *testing.T) {
	a, b := testCrypto(t)
	packets := [][]byte{sealed(t, a, 65534, 10), sealed(t, a, 65535, 10), sealed(t, a, 0, 10), sealed(t, a, 1, 10), sealed(t, a, 0, 20)}
	for _, i := range []int{0, 2, 1, 4, 3} {
		p, e := b.Unprotect(packets[i])
		if e != nil || !bytes.Equal(p.Payload, []byte{1, 2, 3, 4}) {
			t.Fatalf("packet %d: %v", i, e)
		}
	}
	if _, e := b.Unprotect(packets[1]); e == nil {
		t.Fatal("old-ROC replay accepted")
	}
}
func TestSrtpRejectsOutsideReplayWindow(t *testing.T) {
	a, b := testCrypto(t)
	old := sealed(t, a, 1, 10)
	if _, e := b.Unprotect(sealed(t, a, 200, 10)); e != nil {
		t.Fatal(e)
	}
	if _, e := b.Unprotect(old); e == nil {
		t.Fatal("stale packet accepted")
	}
}
func TestSrtpMalformedAndKeyValidation(t *testing.T) {
	_, b := testCrypto(t)
	for n := 0; n < 16; n++ {
		if _, e := b.Unprotect(make([]byte, n)); e == nil {
			t.Fatalf("accepted length %d", n)
		}
	}
	for _, km := range []core.SrtpKeyingMaterial{{}, {MasterKey: make([]byte, 16)}, {MasterKey: make([]byte, 15), MasterSalt: make([]byte, 14)}} {
		if _, e := NewSrtpContext(km, 4); e == nil {
			t.Fatal("invalid key accepted")
		}
	}
}
