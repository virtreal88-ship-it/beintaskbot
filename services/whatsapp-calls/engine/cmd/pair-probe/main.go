// One bounded, unpaired connection probe. Never prints QR, auth state or messages.
package main

import (
	"context"
	"database/sql"
	"flag"
	"fmt"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	_ "modernc.org/sqlite"
	"time"
)

func main() {
	refresh := flag.Bool("refresh-version", false, "fetch supported web version before single probe")
	flag.Parse()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if *refresh {
		v, e := whatsmeow.GetLatestVersion(ctx, nil)
		if e != nil {
			fmt.Println("version_lookup_failed")
			return
		}
		store.SetWAVersion(*v)
		fmt.Println("version_refreshed")
	}
	db, e := sql.Open("sqlite", "file:probe?mode=memory&cache=shared&_pragma=foreign_keys(1)")
	if e != nil {
		fmt.Println("db_open_failed")
		return
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	container := sqlstore.NewWithDB(db, "sqlite3", waLog.Noop)
	if container.Upgrade(ctx) != nil {
		fmt.Println("db_schema_failed")
		return
	}
	cli := whatsmeow.NewClient(container.NewDevice(), waLog.Noop)
	cli.EnableAutoReconnect = false
	result := make(chan string, 1)
	emit := func(code string) {
		select {
		case result <- code:
		default:
		}
	}
	cli.AddEventHandler(func(e any) {
		switch v := e.(type) {
		case *events.QR:
			emit("qr_received")
		case *events.ClientOutdated:
			emit("client_outdated_405")
		case *events.ConnectFailure:
			emit(fmt.Sprintf("connect_failure_%d", int(v.Reason)))
		case *events.Disconnected:
			emit("disconnected")
		case *events.StreamError:
			emit("stream_error")
		}
	})
	if cli.Connect() != nil {
		fmt.Println("connect_failed")
		return
	}
	defer cli.Disconnect()
	select {
	case code := <-result:
		fmt.Println(code)
	case <-ctx.Done():
		fmt.Println("probe_timeout")
	}
}
