package unified

import (
	"errors"
	"io"
	"net/http"
	"net/netip"
	"strings"
	"testing"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestMediaRejectsPrivateAddressesAndUntrustedOrigins(t *testing.T) {
	for _, raw := range []string{"127.0.0.1", "10.1.2.3", "172.21.128.1", "192.168.1.1", "169.254.169.254", "100.64.1.1", "::1", "::ffff:127.0.0.1", "fc00::1", "fe80::1", "0.1.1.1", "198.18.1.1", "2001:db8::1"} {
		if publicMediaIP(netip.MustParseAddr(raw)) {
			t.Errorf("private/reserved address admitted: %s", raw)
		}
	}
	for _, raw := range []string{"https://mmg.whatsapp.net.evil.invalid/a", "http://mmg.whatsapp.net/a", "https://mmg.whatsapp.net:8443/a", "https://user:password@mmg.whatsapp.net/a", "https://127.0.0.1/a", "https://example.invalid/a"} {
		called := false
		transport := mediaTransport{base: roundTripFunc(func(*http.Request) (*http.Response, error) {
			called = true
			return nil, errors.New("unexpected transport")
		}), limit: 20}
		r, _ := http.NewRequest("GET", raw, nil)
		_, err := transport.RoundTrip(r)
		if !errors.Is(err, ErrMediaRestricted) || called {
			t.Errorf("untrusted URL reached transport: %s", raw)
		}
	}
}

func TestMediaBoundsActualChunkedBodyAndDeniesRedirects(t *testing.T) {
	for _, size := range []int{19, 20, 21, 1000} {
		transport := mediaTransport{base: roundTripFunc(func(*http.Request) (*http.Response, error) {
			return &http.Response{StatusCode: 200, Header: make(http.Header), ContentLength: -1, Body: io.NopCloser(strings.NewReader(strings.Repeat("a", size)))}, nil
		}), limit: 20}
		r, _ := http.NewRequest("GET", "https://mmg.whatsapp.net/media", nil)
		response, err := transport.RoundTrip(r)
		if err != nil {
			t.Fatal(err)
		}
		data, err := io.ReadAll(response.Body)
		response.Body.Close()
		if size <= 20 && (err != nil || len(data) != size) {
			t.Fatal("valid bounded media rejected")
		}
		if size > 20 && (!errors.Is(err, ErrMediaSize) || len(data) > 20) {
			t.Fatal("chunked body exceeded cap")
		}
	}
	client := NewMediaHTTPClient()
	if client.CheckRedirect(nil, nil) == nil {
		t.Fatal("redirect allowed")
	}
}
