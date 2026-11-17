package main

// Two cross-process work slots bound raw PCM/finalization/upload memory on the
// shared 1 GiB service. A slot survives failed final writes and is released by
// process exit. It does not grant access to another source's recordings.
import (
	"context"
	"errors"
	"io/fs"
	"net/url"
	"os"
	"path/filepath"
	"runtime/debug"
	"strconv"
	"time"
)

func (s *recordingStore) acquireWork(checkStorage bool) (*os.File, error) {
	if !s.cfg.Managed {
		return nil, nil
	}
	root := s.cfg.CapacityDir
	if !filepath.IsAbs(root) {
		return nil, errors.New("recording_capacity_unconfigured")
	}
	var slot *os.File
	for i := 0; i < 2; i++ {
		f, e := lockRuntime(filepath.Join(root, ".recording-work-"+strconv.Itoa(i)))
		if e == nil {
			slot = f
			break
		}
	}
	if slot == nil {
		return nil, errors.New("recording_capacity_busy")
	}
	if checkStorage {
		var size int64
		err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if d.Type()&os.ModeSymlink != 0 {
				return errors.New("recording_storage_invalid")
			}
			if !d.IsDir() {
				info, e := d.Info()
				if e != nil {
					return e
				}
				size += info.Size()
			}
			if size > 512*1024*1024 {
				return errors.New("recording_storage_limit")
			}
			return nil
		})
		if err != nil {
			slot.Close()
			return nil, err
		}
	}
	return slot, nil
}
func releaseWork(slot *os.File) {
	if slot != nil {
		slot.Close()
	}
}

func (g *appGateway) uploadRecording(ctx context.Context, provider, call string) {
	defer g.uploading.Store(false)
	slot, err := g.server.recordings.acquireWork(false)
	if err != nil {
		return
	}
	defer releaseWork(slot)
	defer debug.FreeOSMemory()
	data, err := g.bundle(provider)
	if err != nil {
		return
	}
	_, _ = g.request(ctx, "/recording?call_id="+url.QueryEscape(call), "application/zip", data)
	// Only the subsequent authenticated sync acknowledgement permits disposal.
}

func (g *appGateway) canUpload(provider string) bool {
	var n int
	err := g.server.recordings.db.QueryRow("SELECT count(*) FROM recordings WHERE session_id=? AND call_id=? AND state IN ('ready','incomplete') AND encrypted IS NOT NULL AND expires_at>?", g.cfg.Session, provider, time.Now().UnixMilli()).Scan(&n)
	return err == nil && n == 1
}
func (g *appGateway) acknowledgeRecording(provider string) {
	if !g.cfg.Managed {
		return
	}
	_, _ = g.server.recordings.db.Exec("UPDATE recordings SET encrypted=NULL,state='delivered' WHERE session_id=? AND call_id=? AND state IN ('ready','incomplete')", g.cfg.Session, provider)
}
