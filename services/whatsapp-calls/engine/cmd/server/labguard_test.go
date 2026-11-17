package main

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestLabGuard(t *testing.T) {
	token := strings.Repeat("x", 40)
	g, e := newLabGuard("http://127.0.0.1:8080", token)
	if e != nil {
		t.Fatal(e)
	}
	for _, tc := range []struct {
		name, method, path, host, origin, pass string
		want                                   int
	}{
		{"authorized", "GET", "/api/events?clientId=other", "127.0.0.1:8080", "", token, 204},
		{"missing auth", "GET", "/api/events", "127.0.0.1:8080", "", "", 401},
		{"cross origin", "POST", "/api/sessions", "127.0.0.1:8080", "https://evil.invalid", token, 403},
		{"rebind", "GET", "/api/events", "evil.invalid", "", token, 403},
		{"no outgoing", "POST", "/api/sessions/s1/calls", "127.0.0.1:8080", "", token, 403},
		{"accept", "POST", "/api/sessions/s1/calls/c1/accept", "127.0.0.1:8080", "http://127.0.0.1:8080", token, 204},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := httptest.NewRequest(tc.method, "http://127.0.0.1:8080"+tc.path, nil)
			r.Host = tc.host
			r.Header.Set("Origin", tc.origin)
			r.Header.Set("X-Client-Id", "attacker")
			if tc.pass != "" {
				r.SetBasicAuth("lab", tc.pass)
			}
			w := httptest.NewRecorder()
			g.wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if clientID(r) != "lab-owner" {
					t.Fatal("untrusted identity")
				}
				w.WriteHeader(204)
			})).ServeHTTP(w, r)
			if w.Code != tc.want {
				t.Fatalf("got %d want %d", w.Code, tc.want)
			}
			if w.Header().Get("Access-Control-Allow-Origin") != "" {
				t.Fatal("CORS exposure")
			}
		})
	}
}

func TestLabGuardCrossSiteEntryDoesNotPermitAPI(t *testing.T) {
	token := strings.Repeat("x", 40)
	g, _ := newLabGuard("http://127.0.0.1:8080", token)
	for _, path := range []string{"/", "/api/sessions", "/api/events"} {
		r := httptest.NewRequest("GET", "http://127.0.0.1:8080"+path, nil)
		r.SetBasicAuth("lab", token)
		r.Header.Set("Sec-Fetch-Site", "cross-site")
		r.Header.Set("Sec-Fetch-Mode", "navigate")
		r.Header.Set("Sec-Fetch-Dest", "document")
		w := httptest.NewRecorder()
		g.wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) })).ServeHTTP(w, r)
		want := 403
		if path == "/" {
			want = 204
		}
		if w.Code != want {
			t.Fatalf("path %s: %d", path, w.Code)
		}
	}
}

func TestLabBrowserFormCookieAndExpiry(t *testing.T) {
	token := strings.Repeat("x", 40)
	g, _ := newLabGuard("http://127.0.0.1:8080", token)
	handler := g.wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) }))
	get := httptest.NewRequest("GET", g.origin+"/", nil)
	page := httptest.NewRecorder()
	handler.ServeHTTP(page, get)
	if page.Code != 200 || !strings.Contains(page.Body.String(), `type="password"`) || page.Header().Get("WWW-Authenticate") != "" {
		t.Fatal("usable login form missing")
	}
	for _, pass := range []string{"wrong", token} {
		r := httptest.NewRequest("POST", g.origin+"/lab/login", strings.NewReader(url.Values{"password": {pass}}.Encode()))
		r.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		r.Header.Set("Origin", g.origin)
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if pass != token {
			if w.Code != 401 || len(w.Result().Cookies()) != 0 {
				t.Fatal("invalid login accepted")
			}
			continue
		}
		if w.Code != 303 || w.Header().Get("Location") != "/" {
			t.Fatal("no login redirect")
		}
		cookies := w.Result().Cookies()
		if len(cookies) != 1 {
			t.Fatal("missing session cookie")
		}
		c := cookies[0]
		if !c.HttpOnly || c.SameSite != http.SameSiteStrictMode || c.MaxAge != 7200 || c.Value == token {
			t.Fatal("unsafe cookie")
		}
		api := httptest.NewRequest("GET", g.origin+"/api/sessions", nil)
		api.AddCookie(c)
		out := httptest.NewRecorder()
		handler.ServeHTTP(out, api)
		if out.Code != 204 {
			t.Fatal("cookie failed")
		}
		for hash := range g.cookies {
			g.cookies[hash] = time.Now().Add(-time.Second)
		}
		out = httptest.NewRecorder()
		handler.ServeHTTP(out, api)
		if out.Code != 401 {
			t.Fatal("expired cookie accepted")
		}
	}
}
func TestLabLoginRequiresSameOrigin(t *testing.T) {
	g, _ := newLabGuard("http://127.0.0.1:8080", strings.Repeat("x", 40))
	r := httptest.NewRequest("POST", g.origin+"/lab/login", strings.NewReader("password=x"))
	w := httptest.NewRecorder()
	g.wrap(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { t.Fatal("unexpected app call") })).ServeHTTP(w, r)
	if w.Code != 403 {
		t.Fatal("originless login accepted")
	}
}

func TestSecondLoginDoesNotInvalidateFirstBrowser(t *testing.T) {
	token := strings.Repeat("x", 40)
	g, _ := newLabGuard("http://127.0.0.1:8080", token)
	handler := g.wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) }))
	var cookies []*http.Cookie
	for i := 0; i < 2; i++ {
		r := httptest.NewRequest("POST", g.origin+"/lab/login", strings.NewReader(url.Values{"password": {token}}.Encode()))
		r.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		r.Header.Set("Origin", g.origin)
		r.Header.Set("Accept", "application/json")
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != 204 {
			t.Fatal("login failed")
		}
		cookies = append(cookies, w.Result().Cookies()[0])
	}
	for _, cookie := range cookies {
		r := httptest.NewRequest("GET", g.origin+"/api/sessions", nil)
		r.AddCookie(cookie)
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != 204 {
			t.Fatal("another login invalidated browser")
		}
	}
}
