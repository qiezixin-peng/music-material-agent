package main

import "testing"

func TestNormalizeAlbumDatePreservesTwoDigitDay(t *testing.T) {
	tests := map[string]string{
		"2020-04-30":   "2020-04-30",
		"2001/06/14":   "2001-06-14",
		"2025年11月17日": "2025-11-17",
		"2000-01-07":   "2000-01-07",
	}
	for input, want := range tests {
		if got := normalizeAlbumDate(input); got != want {
			t.Fatalf("normalizeAlbumDate(%q) = %q, want %q", input, got, want)
		}
	}
}
