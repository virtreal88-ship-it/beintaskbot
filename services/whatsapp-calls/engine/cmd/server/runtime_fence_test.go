package main

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func TestRuntimeFenceChild(t *testing.T) {
	path := os.Getenv("BEIN_TEST_FENCE_PATH")
	if path == "" {
		return
	}
	var e error
	processRuntimeLock, e = lockRuntime(path)
	if e != nil {
		os.Exit(5)
	}
	if os.WriteFile(path+".ready", []byte("ready"), 0600) != nil {
		os.Exit(6)
	}
	for {
		time.Sleep(time.Second)
	}
}

func TestRuntimeFenceRequiresActualProcessExit(t *testing.T) {
	path := filepath.Join(t.TempDir(), "runtime.lock")
	cmd := exec.Command(os.Args[0], "-test.run=^TestRuntimeFenceChild$")
	cmd.Env = append(os.Environ(), "BEIN_TEST_FENCE_PATH="+path)
	if e := cmd.Start(); e != nil {
		t.Fatal(e)
	}
	defer func() {
		if cmd.Process != nil {
			_ = cmd.Process.Kill()
		}
	}()
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, e := os.Stat(path + ".ready"); e == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("child not ready")
		}
		time.Sleep(10 * time.Millisecond)
	}
	if lock, e := lockRuntime(path); e == nil {
		lock.Close()
		t.Fatal("two processes admitted")
	}
	if e := cmd.Process.Kill(); e != nil {
		t.Fatal(e)
	}
	_ = cmd.Wait()
	lock, e := lockRuntime(path)
	if e != nil {
		t.Fatal("exited process retained runtime fence")
	}
	lock.Close()
}

func TestManagedRuntimeJournalNeedsFenceAndCarriesClosedBoots(t *testing.T) {
	legacy, _ := testGateway(t)
	server := legacy.server
	config := legacy.cfg
	config.Managed = true
	server.recordings.cfg.Managed = true
	raw, _ := json.Marshal(config)
	path := filepath.Join(t.TempDir(), "managed.json")
	os.WriteFile(path, raw, 0600)
	if _, e := newAppGateway(server, path); e == nil {
		t.Fatal("unfenced process admitted")
	}
	server.runtimeExclusive = true
	first, e := newAppGateway(server, path)
	if e != nil {
		t.Fatal(e)
	}
	second, e := newAppGateway(server, path)
	if e != nil {
		t.Fatal(e)
	}
	if len(first.retired) != 0 || len(second.retired) != 1 || second.retired[0] != first.boot || second.boot == first.boot {
		t.Fatal("runtime journal lost identity")
	}
}
