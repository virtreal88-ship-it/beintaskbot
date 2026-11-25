package unified

import "encoding/binary"

// pttSeconds reads the sample count from the final page of the canonical
// single-stream, mono Ogg Opus produced and decoded by the Worker. Opus
// granules always count samples at 48 kHz, regardless of input sample rate.
// Subtract the encoder pre-skip; round up so a playable sub-second note does
// not appear as a zero-second note. This is not a general-purpose decoder.
func pttSeconds(data []byte) (uint32, error) {
	const rate, maxSeconds = 48000, 120
	var serial, seq uint32
	var preSkip uint64
	for len(data) >= 27 {
		if string(data[:4]) != "OggS" || data[4] != 0 || data[5]&^byte(7) != 0 {
			break
		}
		segments := int(data[26])
		if len(data) < 27+segments {
			break
		}
		end := 27 + segments
		for _, size := range data[27:end] {
			end += int(size)
		}
		if end > len(data) || binary.LittleEndian.Uint32(data[18:22]) != seq {
			break
		}
		pageSerial := binary.LittleEndian.Uint32(data[14:18])
		if seq == 0 {
			head := data[27+segments : end]
			if data[5] != 2 || segments != 1 || len(head) != 19 ||
				string(head[:8]) != "OpusHead" || head[8] != 1 || head[9] != 1 || head[18] != 0 {
				break
			}
			serial, preSkip = pageSerial, uint64(binary.LittleEndian.Uint16(head[10:12]))
		} else if pageSerial != serial || data[5]&2 != 0 {
			break
		}
		if data[5]&4 != 0 {
			granule := binary.LittleEndian.Uint64(data[6:14])
			if seq < 2 || end != len(data) || granule <= preSkip || granule-preSkip > rate*maxSeconds {
				break
			}
			return uint32((granule - preSkip + rate - 1) / rate), nil
		}
		seq++
		data = data[end:]
	}
	return 0, ErrMediaRestricted
}
