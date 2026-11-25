package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store"
)

// Never release this lock while any goroutine can still own provider transports.
// The OS closes it on process exit, including crashes and SIGKILL.
var processRuntimeLock *os.File

func main() {
	addr := flag.String("addr", "127.0.0.1:8080", "HTTP listen address")
	dbPath := flag.String("db", "wacalls.db", "SQLite session database path")
	staticDir := flag.String("static", "client/dist", "static client directory (optional)")
	debug := flag.Bool("debug", false, "verbose logging")
	maxCalls := flag.Int("max-calls-per-session", 1, "max concurrent calls per session (0 = unlimited)")
	tokenFile := flag.String("lab-token-file", "", "required private file containing the isolated pilot password")
	recordingConfigFile := flag.String("recording-config", "", "private test recording configuration (disabled if absent)")
	appGatewayConfigFile := flag.String("app-gateway-config", "", "private scoped Beintaskbot gateway configuration")
	flag.Parse()
	host, _, addrErr := net.SplitHostPort(*addr)
	if addrErr != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() || *debug || *maxCalls != 1 {
		fmt.Fprintln(os.Stderr, "pilot requires a loopback address, debug disabled and one call per session")
		os.Exit(1)
	}
	tokenBytes, tokenErr := os.ReadFile(*tokenFile)
	token := strings.TrimSpace(string(tokenBytes))
	if tokenErr != nil || len(token) < 32 {
		fmt.Fprintln(os.Stderr, "private pilot token file required (minimum 32 characters)")
		os.Exit(1)
	}
	guard, guardErr := newLabGuard("http://"+*addr, token)
	if guardErr != nil {
		fmt.Fprintln(os.Stderr, "invalid pilot configuration")
		os.Exit(1)
	}
	processRuntimeLock, tokenErr = lockRuntime(*dbPath + ".runtime.lock")
	if tokenErr != nil {
		fmt.Fprintln(os.Stderr, "call_runtime_already_owned")
		os.Exit(1)
	}

	level := slog.LevelInfo
	if *debug {
		level = slog.LevelDebug
	}
	log := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: level, ReplaceAttr: safeLabLogAttr}))
	slog.SetDefault(log)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Set once before constructing any clients. A bundled web revision can be
	// rejected with 405 before WhatsApp ever supplies a pairing QR.
	versionCtx, versionCancel := context.WithTimeout(ctx, 15*time.Second)
	version, versionErr := whatsmeow.GetLatestVersion(versionCtx, nil)
	versionCancel()
	if versionErr != nil || version == nil {
		log.Error("whatsapp_version_lookup_failed")
		os.Exit(1)
	}
	store.SetWAVersion(*version)
	log.Info("whatsapp_version_refreshed")

	srv, err := newServer(ctx, *dbPath, *staticDir, *maxCalls, log)
	if err != nil {
		log.Error("startup failed", "err", err)
		os.Exit(1)
	}
	srv.runtimeExclusive = true
	defer func() { srv.sessions.disconnectAll(); srv.recordings.flushPending() }()
	srv.recordings, err = newRecordingStore(ctx, srv.sessions.store.db, *recordingConfigFile)
	if err != nil {
		log.Error("startup failed")
		os.Exit(1)
	}
	srv.sessions.recordings = srv.recordings
	srv.sessions.messageLab, err = newMessageLab(ctx, srv.sessions.store.db, *appGatewayConfigFile)
	if err != nil {
		log.Error("startup failed")
		os.Exit(1)
	}
	if err := srv.sessions.messageLab.prepareCallTests(srv); err != nil {
		log.Error("test_call_configuration_invalid")
		os.Exit(1)
	}

	srv.appGateway, err = newAppGateway(srv, *appGatewayConfigFile)
	if err != nil {
		log.Error("startup failed")
		os.Exit(1)
	}
	srv.sessions.nativeGateway = srv.appGateway
	if err := srv.sessions.Restore(ctx); err != nil {
		log.Error("session restore failed", "err", err)
		os.Exit(1)
	}
	guard.appControlled = srv.appGateway != nil
	go srv.appGateway.run(ctx)
	go srv.appGateway.deliverMessages(ctx)
	go srv.appGateway.refreshGroups(ctx)

	var handler http.Handler = guard.wrap(srv.routes())
	maxHeaderBytes := 16 << 10
	if srv.appGateway != nil && srv.appGateway.cfg.Managed {
		handler = managedHandler(srv, *addr, token)
		maxHeaderBytes = 32 << 10
	}
	httpSrv := &http.Server{Addr: *addr, Handler: handler, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: maxHeaderBytes}
	go func() {
		log.Info("HTTP server listening", "addr", *addr)
		if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("http server error", "err", err)
		}
	}()

	<-ctx.Done()
	log.Info("shutting down")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = httpSrv.Shutdown(shutdownCtx)
}
