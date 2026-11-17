//go:build windows

package main

import (
	"golang.org/x/sys/windows"
	"os"
)

func lockRuntime(path string) (*os.File, error) {
	file, e := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, e
	}
	e = windows.LockFileEx(windows.Handle(file.Fd()), windows.LOCKFILE_EXCLUSIVE_LOCK|windows.LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0, &windows.Overlapped{})
	if e != nil {
		file.Close()
		return nil, e
	}
	return file, nil
}
