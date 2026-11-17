package main

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

// This gate is ONLY for the single-operator isolated experiment. It is not
// Beintaskbot tenant authorization and must not be exposed as a public service.
type labGuard struct {
	appControlled bool
	origin, host  string
	tokenHash     [32]byte
	mu            sync.Mutex
	cookies       map[[32]byte]time.Time
}

func newLabGuard(origin, token string) (*labGuard, error) {
	u, e := url.Parse(origin)
	if e != nil || u.Scheme != "http" || u.Host == "" || u.Path != "" || len(token) < 32 {
		return nil, fmt.Errorf("invalid pilot access configuration")
	}
	return &labGuard{origin: origin, host: u.Host, tokenHash: sha256.Sum256([]byte(token)), cookies: make(map[[32]byte]time.Time)}, nil
}
func (g *labGuard) wrap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("Content-Security-Policy", "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'")
		// Exact Host stops DNS rebinding; exact Origin and Fetch Metadata stop CSRF.
		if r.Host != g.host || (r.Header.Get("Origin") != "" && r.Header.Get("Origin") != g.origin) || (r.Header.Get("Sec-Fetch-Site") == "cross-site" && !(r.Method == http.MethodGet && r.URL.Path == "/" && r.Header.Get("Sec-Fetch-Mode") == "navigate" && r.Header.Get("Sec-Fetch-Dest") == "document")) {
			http.Error(w, "pilot_origin_denied", http.StatusForbidden)
			return
		}
		if r.URL.Path == "/lab/login" && r.Method == http.MethodPost {
			if r.Header.Get("Origin") != g.origin {
				http.Error(w, "pilot_origin_denied", http.StatusForbidden)
				return
			}
			r.Body = http.MaxBytesReader(w, r.Body, 4096)
			if r.ParseForm() != nil {
				http.Error(w, "invalid_login_form", http.StatusBadRequest)
				return
			}
			candidate := sha256.Sum256([]byte(r.PostForm.Get("password")))
			if subtle.ConstantTimeCompare(candidate[:], g.tokenHash[:]) != 1 {
				g.loginPage(w, 401, true)
				return
			}
			raw := make([]byte, 32)
			if _, err := rand.Read(raw); err != nil {
				http.Error(w, "login_unavailable", 503)
				return
			}
			value := base64.RawURLEncoding.EncodeToString(raw)
			expires := time.Now().Add(2 * time.Hour)
			g.mu.Lock()
			for hash, until := range g.cookies {
				if !time.Now().Before(until) {
					delete(g.cookies, hash)
				}
			}
			if len(g.cookies) >= 16 {
				g.mu.Unlock()
				http.Error(w, "login_session_limit", 429)
				return
			}
			g.cookies[sha256.Sum256([]byte(value))] = expires
			g.mu.Unlock()
			// HTTP is intentional only on loopback inside the SSH tunnel.
			http.SetCookie(w, &http.Cookie{Name: "wa_lab_session", Value: value, Path: "/", HttpOnly: true, SameSite: http.SameSiteStrictMode, MaxAge: 7200, Expires: expires})
			if r.Header.Get("Accept") == "application/json" {
				w.WriteHeader(http.StatusNoContent)
				return
			}
			http.Redirect(w, r, "/", http.StatusSeeOther)
			return
		}
		user, pass, ok := r.BasicAuth()
		candidate := sha256.Sum256([]byte(pass))
		authorized := ok && user == "lab" && subtle.ConstantTimeCompare(candidate[:], g.tokenHash[:]) == 1
		if cookie, err := r.Cookie("wa_lab_session"); err == nil {
			digest := sha256.Sum256([]byte(cookie.Value))
			g.mu.Lock()
			until, known := g.cookies[digest]
			valid := known && time.Now().Before(until)
			g.mu.Unlock()
			authorized = authorized || valid
		}
		if !authorized {
			if r.Method == http.MethodGet && r.URL.Path == "/" {
				g.loginPage(w, 200, false)
				return
			}
			// Do not use WWW-Authenticate: Chrome's native dialog can leave an
			// inaccessible blank error page. The HTML form owns browser login.
			http.Error(w, "pilot_auth_required", http.StatusUnauthorized)
			return
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
		if g.appControlled && r.Method != http.MethodGet && strings.HasPrefix(r.URL.Path, "/api/") {
			http.Error(w, "use_beintaskbot_app", http.StatusForbidden)
			return
		}
		// Incoming-only experiment. Even an authorized browser cannot place a call.
		if r.Method == http.MethodPost && len(parts) == 4 && parts[0] == "api" && parts[1] == "sessions" && parts[3] == "calls" {
			http.Error(w, "pilot_outgoing_disabled", http.StatusForbidden)
			return
		}
		// Identity is server-assigned in this single-owner lab, never trusted from JS.
		r.Header.Set("X-Client-Id", "lab-owner")
		q := r.URL.Query()
		q.Del("clientId")
		r.URL.RawQuery = q.Encode()
		r.Body = http.MaxBytesReader(w, r.Body, 128<<10)
		next.ServeHTTP(w, r)
	})
}

func (g *labGuard) loginPage(w http.ResponseWriter, code int, invalid bool) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(code)
	message := "Введите пароль из локального файла pilot-browser-login.txt. Пароль от Ubuntu здесь не используется."
	if invalid {
		message = "Пароль не совпал. Скопируйте только значение после Password: из файла pilot-browser-login.txt."
	}
	fmt.Fprintf(w, `<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Вход в тест звонков Beintaskbot</title>
 <style>body{font:18px system-ui;background:#f5f7fa;color:#18202a;margin:0;padding:30px}main{max-width:460px;margin:8vh auto;background:white;padding:30px;border-radius:18px}label,input,button{display:block}input{box-sizing:border-box;width:100%%;padding:12px;margin:8px 0 20px;font-size:18px}button{padding:12px 24px;background:#176f48;color:white;border:0;border-radius:9px;font-size:18px}p{line-height:1.5}</style>
 <main><h1>Тест звонков Beintaskbot</h1><p>Отдельная тестовая среда. Рабочие номера не подключайте.</p><p role="status">%s</p><form action="/lab/login" method="post"><label for="password">Пароль тестового интерфейса</label><input id="password" name="password" type="password" autocomplete="current-password" required autofocus><button type="submit">Войти</button></form></main><script>
const form=document.querySelector('form'), status=document.querySelector('[role="status"]'), button=form.querySelector('button');
form.addEventListener('submit',async event=>{
 event.preventDefault();button.disabled=true;status.textContent='Проверяю вход…';
 try {
  const response=await fetch('/lab/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'},body:new URLSearchParams(new FormData(form))});
  if(response.status===401){status.textContent='Пароль не совпал. Нужен пароль из pilot-browser-login.txt, не пароль Ubuntu.';return;}
  if(!response.ok){status.textContent='Вход не выполнен (код '+response.status+'). Пароль повторно пока не вводите.';return;}
  const check=await fetch('/api/sessions',{credentials:'same-origin',cache:'no-store'});
  if(check.ok){window.location.replace('/');return;}
  status.textContent='Пароль принят, но эта вкладка не сохранила вход. Откройте страницу в обычном Chrome.';
 }catch(error){status.textContent='Нет соединения с тестовым сервером. Введённый пароль сохранён в поле.';}
 finally{button.disabled=false;}
});
</script></html>`, message)
}

// Third-party message/error strings can contain JIDs, keys or packet bytes.
// Retain severity and time only; our allowlisted lifecycle messages are safe.
func safeLabLogAttr(groups []string, a slog.Attr) slog.Attr {
	if len(groups) > 0 {
		return slog.Attr{}
	}
	switch a.Key {
	case slog.TimeKey, slog.LevelKey:
		return a
	case slog.MessageKey:
		switch a.Value.String() {
		case "HTTP server listening", "shutting down", "startup failed", "session restore failed", "http server error", "whatsapp_version_lookup_failed", "whatsapp_version_refreshed":
			return a
		}
		return slog.String(slog.MessageKey, "call_component_event")
	default:
		return slog.Attr{}
	}
}
