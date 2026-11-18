package unified

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"net/url"
	"strings"
	"time"
)

var ErrDeliveryUnconfirmed = errors.New("unified_delivery_unconfirmed")

// DeliverOnce transfers one retained event, using the existing managed gateway
// credential. A stored receipt advances only this journal's exact cursor. It
// neither authorizes a device owner nor deletes an event or projects a CRM row.
func (j *Journal) DeliverOnce(ctx context.Context, transport *http.Client, endpoint, token string) (bool, error) {
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "/api/platform/call-gateway/messages" || len(token) < 32 || len(token) > 256 || strings.ContainsAny(token, "\r\n") || transport == nil {
		return false, ErrConfiguration
	}
	delivery, err := j.NextDelivery(ctx)
	if err != nil {
		return false, err
	}
	if delivery == nil {
		return false, nil
	}
	raw, err := json.Marshal(delivery)
	if err != nil {
		return false, ErrDeliveryUnconfirmed
	}
	request, err := http.NewRequestWithContext(ctx, "POST", u.String(), bytes.NewReader(raw))
	if err != nil {
		return false, ErrConfiguration
	}
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	client := *transport
	client.Jar = nil // Source credentials only; never ambient browser cookies.
	// Never follow a redirect carrying source credentials or protocol payloads.
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	if client.Timeout <= 0 || client.Timeout > 12*time.Second {
		client.Timeout = 12 * time.Second
	}
	response, err := client.Do(request)
	if err != nil {
		return false, ErrDeliveryUnconfirmed
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return false, ErrDeliveryUnconfirmed
	}
	contentType, _, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil || contentType != "application/json" {
		return false, ErrDeliveryUnconfirmed
	}
	raw, err = io.ReadAll(io.LimitReader(response.Body, 8193))
	if err != nil || len(raw) > 8192 {
		return false, ErrDeliveryUnconfirmed
	}
	var receipt StoredReceipt
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&receipt) != nil || decoder.Decode(new(any)) != io.EOF {
		return false, ErrDeliveryUnconfirmed
	}
	if receipt.After != delivery.After || receipt.Sequence != delivery.Event.Sequence || receipt.EventID != delivery.Event.ID {
		return false, ErrDeliveryUnconfirmed
	}
	if err = j.ConfirmStored(ctx, receipt); err != nil {
		return false, err
	}
	return true, nil
}
