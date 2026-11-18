package unified

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"time"

	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
)

// EncryptedBuffer preserves whatsmeow's decryption+ratchet transaction while
// ensuring its retry/outgoing buffers do not persist message plaintext. Install
// before connecting the experimental client, never replace a running store.
type EncryptedBuffer struct {
	store.EventBuffer
	Journal *Journal
}

type encryptedDeviceContainer struct {
	store.DeviceContainer
	journal *Journal
	account string
}

// ProtectDevice must run before Connect. A new QR pairing initializes stores
// inside PutDevice and otherwise overwrites an EventBuffer wrapper installed on
// the unpaired device. Re-wrap before Save returns, not in a later Connected
// callback, which could race with the first incoming message.
func ProtectDevice(device *store.Device, journal *Journal, account string) {
	container := &encryptedDeviceContainer{device.Container, journal, account}
	container.install(device)
}

func (c *encryptedDeviceContainer) install(device *store.Device) {
	device.Container = c
	if device.EventBuffer != nil {
		if _, already := device.EventBuffer.(EncryptedBuffer); !already {
			device.EventBuffer = EncryptedBuffer{device.EventBuffer, c.journal}
		}
	}
}

func (c *encryptedDeviceContainer) PutDevice(ctx context.Context, device *store.Device) error {
	if device.ID == nil || device.ID.ToNonAD().String() != c.account {
		return ErrConfiguration
	}
	err := c.DeviceContainer.PutDevice(ctx, device)
	c.install(device)
	return err
}

func (b EncryptedBuffer) GetBufferedEvent(ctx context.Context, hash [32]byte) (*store.BufferedEvent, error) {
	event, err := b.EventBuffer.GetBufferedEvent(ctx, hash)
	if err != nil || event == nil || event.Plaintext == nil {
		return event, err
	}
	plain, err := b.Journal.BufferOpen("incoming", hex.EncodeToString(hash[:]), event.Plaintext)
	if err != nil {
		return nil, ErrConfiguration
	}
	copy := *event
	copy.Plaintext = plain
	return &copy, nil
}

func (b EncryptedBuffer) PutBufferedEvent(ctx context.Context, hash [32]byte, plain []byte, timestamp time.Time) error {
	body, err := b.Journal.BufferSeal("incoming", hex.EncodeToString(hash[:]), plain)
	if err != nil {
		return err
	}
	return b.EventBuffer.PutBufferedEvent(ctx, hash, body, timestamp)
}

type outgoingBuffer struct {
	Chat    string
	Payload []byte
}

func (b EncryptedBuffer) AddOutgoingEvent(ctx context.Context, chat types.JID, id types.MessageID, format string, plain []byte) error {
	payload, _ := json.Marshal(outgoingBuffer{chat.String(), plain})
	body, err := b.Journal.BufferSeal("outgoing:"+format, id, payload)
	if err != nil {
		return err
	}
	return b.EventBuffer.AddOutgoingEvent(ctx, chat, id, format, body)
}

func (b EncryptedBuffer) GetOutgoingEvent(ctx context.Context, chat, alt types.JID, id types.MessageID) (string, []byte, error) {
	format, body, err := b.EventBuffer.GetOutgoingEvent(ctx, chat, alt, id)
	if err != nil || body == nil {
		return format, body, err
	}
	plain, err := b.Journal.BufferOpen("outgoing:"+format, id, body)
	if err != nil {
		return "", nil, ErrConfiguration
	}
	var payload outgoingBuffer
	if json.Unmarshal(plain, &payload) != nil || (payload.Chat != chat.String() && payload.Chat != alt.String()) {
		return "", nil, ErrConfiguration
	}
	return format, payload.Payload, nil
}
