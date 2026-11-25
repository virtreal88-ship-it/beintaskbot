package unified

import (
	"context"
	"crypto/tls"
	"errors"
	"io"
	"net"
	"net/http"
	"net/netip"
	"strings"
	"time"
)

const MaxMediaBytes = 10 << 20
const MaxVideoBytes = 100 << 20

type videoTransferKey struct{}

// A large transport budget is available only to validated MP4 operations.
func WithVideoTransfer(ctx context.Context) context.Context {
	return context.WithValue(ctx, videoTransferKey{}, true)
}

var ErrMediaRestricted = errors.New("unified_media_restricted")
var ErrMediaSize = errors.New("unified_media_size_limit")

// Private/protocol media URLs are never sent to a browser. Resolve only approved
// CDN names, reject private answers, and dial the checked address (no DNS TOCTOU).
func allowedMediaHost(host string) bool {
	host = strings.ToLower(host)
	return host == "mmg.whatsapp.net" || strings.HasSuffix(host, ".whatsapp.net") || strings.HasSuffix(host, ".fbcdn.net")
}

func publicMediaIP(ip netip.Addr) bool {
	ip = ip.Unmap()
	if !ip.IsValid() || !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() {
		return false
	}
	for _, raw := range []string{"0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "240.0.0.0/4", "2001:db8::/32", "2001::/32", "2002::/16", "64:ff9b::/96", "64:ff9b:1::/48"} {
		if netip.MustParsePrefix(raw).Contains(ip) {
			return false
		}
	}
	return true
}

func mediaDial(ctx context.Context, network, address string) (net.Conn, error) {
	host, port, err := net.SplitHostPort(address)
	if err != nil || port != "443" || !allowedMediaHost(host) {
		return nil, ErrMediaRestricted
	}
	ips, err := net.DefaultResolver.LookupNetIP(ctx, "ip", host)
	if err != nil || len(ips) == 0 {
		return nil, ErrMediaRestricted
	}
	for _, ip := range ips {
		if !publicMediaIP(ip) {
			return nil, ErrMediaRestricted
		}
	}
	dialer := net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}
	for _, ip := range ips {
		connection, err := dialer.DialContext(ctx, network, net.JoinHostPort(ip.String(), port))
		if err == nil {
			return connection, nil
		}
	}
	return nil, ErrMediaRestricted
}

type mediaTransport struct {
	base  http.RoundTripper
	limit int64
}

func (t mediaTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	u := request.URL
	if u == nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || (u.Port() != "" && u.Port() != "443") || !allowedMediaHost(u.Hostname()) || (request.Host != "" && request.Host != u.Host) {
		return nil, ErrMediaRestricted
	}
	if request.Method != http.MethodGet && request.Method != http.MethodPost && request.Method != http.MethodDelete {
		return nil, ErrMediaRestricted
	}
	limit := t.limit
	if request.Context().Value(videoTransferKey{}) == true {
		limit = MaxVideoBytes + 64
	}
	if request.ContentLength > limit {
		return nil, ErrMediaSize
	}
	if request.Body != nil {
		request = request.Clone(request.Context())
		request.Body = &boundedMediaBody{ReadCloser: request.Body, remaining: limit}
	}
	response, err := t.base.RoundTrip(request)
	if err != nil {
		return nil, ErrMediaRestricted
	}
	if response.ContentLength > limit || (response.Header.Get("Content-Encoding") != "" && response.Header.Get("Content-Encoding") != "identity") {
		response.Body.Close()
		return nil, ErrMediaSize
	}
	response.Body = &boundedMediaBody{ReadCloser: response.Body, remaining: limit}
	return response, nil
}

type boundedMediaBody struct {
	io.ReadCloser
	remaining int64
}

func (b *boundedMediaBody) Read(p []byte) (int, error) {
	if len(p) == 0 {
		return 0, nil
	}
	if b.remaining == 0 {
		var extra [1]byte
		n, err := b.ReadCloser.Read(extra[:])
		if n != 0 {
			return 0, ErrMediaSize
		}
		return 0, err
	}
	if int64(len(p)) > b.remaining {
		p = p[:b.remaining]
	}
	n, err := b.ReadCloser.Read(p)
	b.remaining -= int64(n)
	return n, err
}

func NewMediaHTTPClient() *http.Client {
	transport := &http.Transport{Proxy: nil, DialContext: mediaDial, ForceAttemptHTTP2: true, DisableCompression: true,
		TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12}, TLSHandshakeTimeout: 10 * time.Second,
		ResponseHeaderTimeout: 15 * time.Second, MaxResponseHeaderBytes: 32 << 10, MaxConnsPerHost: 4,
		MaxIdleConns: 4, IdleConnTimeout: 30 * time.Second}
	return &http.Client{Transport: mediaTransport{base: transport, limit: MaxMediaBytes + 64}, Timeout: 110 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return ErrMediaRestricted }}
}
