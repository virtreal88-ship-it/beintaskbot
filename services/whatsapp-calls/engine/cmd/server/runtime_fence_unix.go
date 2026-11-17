//go:build !windows

package main

import (
	"golang.org/x/sys/unix"
	"os"
)

func lockRuntime(path string) (*os.File, error) {
	file, e := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, e
	}
	if e = unix.Flock(int(file.Fd()), unix.LOCK_EX|unix.LOCK_NB); e != nil {
		file.Close()
		return nil, e
	}
	return file, nil
}
