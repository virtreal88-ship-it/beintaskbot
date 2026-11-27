package unified

import (
	"math"
	"regexp"
	"slices"
	"strings"

	"go.mau.fi/whatsmeow/proto/waE2E"
)

type SharedContact struct {
	Name   string   `json:"name"`
	Phones []string `json:"phones"`
}

type SharedLocation struct {
	Latitude  float64 `json:"latitude"`
	Longitude float64 `json:"longitude"`
	Name      string  `json:"name"`
	Address   string  `json:"address"`
}

var sharedPhone = regexp.MustCompile(`^\+?[0-9]{3,20}$`)
var vcardFold = regexp.MustCompile(`\r?\n[ \t]`)
var phoneSeparators = strings.NewReplacer(" ", "", "-", "", "(", "", ")", "", ".", "")

func sharedLabel(value string, limit int) string {
	value = strings.TrimSpace(strings.ReplaceAll(value, "\x00", ""))
	chars := []rune(value)
	if len(chars) > limit {
		return string(chars[:limit])
	}
	return value
}

func sharedContacts(contacts []*waE2E.ContactMessage) []SharedContact {
	items := make([]SharedContact, 0, min(len(contacts), 32))
	for _, contact := range contacts[:min(len(contacts), 32)] {
		if contact == nil {
			continue
		}
		item := SharedContact{Name: sharedLabel(contact.GetDisplayName(), 256), Phones: []string{}}
		for line := range strings.SplitSeq(vcardFold.ReplaceAllString(contact.GetVcard(), ""), "\n") {
			key, value, ok := strings.Cut(strings.TrimSpace(line), ":")
			if !ok {
				continue
			}
			field, _, _ := strings.Cut(key, ";")
			if i := strings.LastIndexByte(field, '.'); i >= 0 {
				field = field[i+1:]
			}
			switch {
			case strings.EqualFold(field, "FN") && item.Name == "":
				item.Name = sharedLabel(value, 256)
			case strings.EqualFold(field, "TEL") && len(item.Phones) < 12:
				if len(value) >= 4 && strings.EqualFold(value[:4], "tel:") {
					value = value[4:]
				}
				phone := phoneSeparators.Replace(strings.TrimSpace(value))
				if sharedPhone.MatchString(phone) && !slices.Contains(item.Phones, phone) {
					item.Phones = append(item.Phones, phone)
				}
			}
		}
		items = append(items, item)
	}
	return items
}

func sharedLocation(lat, lon *float64, name, address string) *SharedLocation {
	if lat == nil || lon == nil || math.IsNaN(*lat) || math.IsNaN(*lon) ||
		math.Abs(*lat) > 90 || math.Abs(*lon) > 180 {
		return nil
	}
	return &SharedLocation{Latitude: *lat, Longitude: *lon,
		Name: sharedLabel(name, 256), Address: sharedLabel(address, 512)}
}
