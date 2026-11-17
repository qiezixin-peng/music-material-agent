package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/guohuiyuan/music-lib/model"
	"github.com/guohuiyuan/music-lib/soda"
)

type request struct {
	Operation  string     `json:"operation"`
	Cookie     string     `json:"cookie"`
	Query      string     `json:"query,omitempty"`
	Song       *songInput `json:"song,omitempty"`
	OutputPath string     `json:"output_path,omitempty"`
}

type songInput struct {
	ID       string            `json:"id"`
	Name     string            `json:"name,omitempty"`
	Artist   string            `json:"artist,omitempty"`
	Album    string            `json:"album,omitempty"`
	AlbumID  string            `json:"album_id,omitempty"`
	Duration int               `json:"duration,omitempty"`
	Cover    string            `json:"cover,omitempty"`
	Extra    map[string]string `json:"extra,omitempty"`
}

type candidate struct {
	ID                 string            `json:"id"`
	Name               string            `json:"name"`
	Artist             string            `json:"artist"`
	Album              string            `json:"album"`
	AlbumID            string            `json:"album_id,omitempty"`
	Duration           int               `json:"duration,omitempty"`
	Size               int64             `json:"size,omitempty"`
	Bitrate            int               `json:"bitrate,omitempty"`
	Cover              string            `json:"cover,omitempty"`
	ReleaseDate        int64             `json:"release_date,omitempty"`
	ReleaseCompanyRaw  string            `json:"release_company_raw,omitempty"`
	ReleaseCompanyKind string            `json:"release_company_kind,omitempty"`
	Extra              map[string]string `json:"extra,omitempty"`
	IsVIP              bool              `json:"is_vip,omitempty"`
}

type response struct {
	Status     string      `json:"status"`
	Candidates []candidate `json:"candidates,omitempty"`
	Path       string      `json:"path,omitempty"`
	Bytes      int64       `json:"bytes,omitempty"`
	Error      string      `json:"error,omitempty"`
}

var (
	credentialPattern = regexp.MustCompile(`(?i)(cookie|token|authorization|password)(\s*[=:]\s*)[^\s,;]+`)
	urlQueryPattern   = regexp.MustCompile(`(https?://[^?\s]+)\?[^\s]+`)
	numericIDPattern  = regexp.MustCompile(`^[0-9]+$`)
)

func main() {
	var req request
	if err := json.NewDecoder(os.Stdin).Decode(&req); err != nil {
		write(response{Status: "INVALID_REQUEST", Error: "invalid JSON request"})
		return
	}

	client := soda.New(req.Cookie)
	switch strings.ToLower(strings.TrimSpace(req.Operation)) {
	case "search":
		write(search(client, strings.TrimSpace(req.Query)))
	case "resolve":
		write(resolve(client, req.Song))
	case "download":
		write(download(client, req.Song, strings.TrimSpace(req.OutputPath)))
	default:
		write(response{Status: "INVALID_REQUEST", Error: "unsupported operation"})
	}
}

func search(client *soda.Soda, query string) response {
	if query == "" {
		return response{Status: "INVALID_REQUEST", Error: "query is required"}
	}
	songs, err := client.Search(query)
	if err != nil {
		return response{Status: "SEARCH_UNAVAILABLE", Error: safeError(err)}
	}
	items := make([]candidate, 0, len(songs))
	for _, song := range songs {
		items = append(items, candidateFromSong(song))
	}
	return response{Status: "OK", Candidates: items}
}

func resolve(client *soda.Soda, input *songInput) response {
	if input == nil || !numericIDPattern.MatchString(strings.TrimSpace(input.ID)) {
		return response{Status: "INVALID_REQUEST", Error: "numeric soda song id is required"}
	}
	song, err := client.Parse("https://www.qishui.com/track/" + strings.TrimSpace(input.ID))
	if err != nil {
		return response{Status: "RESOLVE_UNAVAILABLE", Error: safeError(err)}
	}
	if song == nil || strings.TrimSpace(song.ID) == "" {
		return response{Status: "RESOLVE_UNAVAILABLE", Error: "Soda returned no track metadata"}
	}
	item := candidateFromSong(*song)
	enrichCandidateFromAlbum(client, &item)
	return response{Status: "OK", Candidates: []candidate{item}}
}

// enrichCandidateFromAlbum follows the album ID exposed by the official track
// response.  Soda renders the release date and copyright/issuing company on the
// album detail page, not on the track search card.
func enrichCandidateFromAlbum(client *soda.Soda, item *candidate) {
	albumID := strings.TrimSpace(item.AlbumID)
	if albumID == "" {
		return
	}
	album, _, err := client.ParseAlbum("https://www.qishui.com/album/" + albumID)
	if err != nil || album == nil {
		return
	}
	if album.Extra != nil {
		item.ReleaseDate, _ = strconv.ParseInt(
			strings.TrimSpace(album.Extra["release_date"]), 10, 64,
		)
	}
	description := strings.TrimSpace(album.Description)
	if description == "" {
		return
	}
	if marker := strings.LastIndex(description, "©"); marker >= 0 {
		item.ReleaseCompanyRaw = strings.TrimSpace(description[marker+len("©"):])
		item.ReleaseCompanyKind = "copyright_notice"
		return
	}
	if marker := strings.LastIndex(description, "℗"); marker >= 0 {
		item.ReleaseCompanyRaw = strings.TrimSpace(description[marker+len("℗"):])
		item.ReleaseCompanyKind = "phonographic_copyright_notice"
		return
	}
	// music-lib uses the platform's album company as Description when the page
	// has no explicit PC footer lines. Avoid treating a date-only line as a label.
	if !strings.Contains(description, "发行时间") {
		item.ReleaseCompanyRaw = description
		item.ReleaseCompanyKind = "platform_company"
	}
}

func candidateFromSong(song model.Song) candidate {
	return candidate{
		ID: song.ID, Name: song.Name, Artist: song.Artist, Album: song.Album,
		AlbumID: song.AlbumID, Duration: song.Duration, Size: song.Size,
		Bitrate: song.Bitrate, Cover: song.Cover,
		Extra: song.Extra, IsVIP: song.IsVIP,
	}
}

func download(client *soda.Soda, input *songInput, outputPath string) response {
	if input == nil || strings.TrimSpace(input.ID) == "" || outputPath == "" {
		return response{Status: "INVALID_REQUEST", Error: "song id and output_path are required"}
	}
	abs, err := filepath.Abs(outputPath)
	if err != nil {
		return response{Status: "INVALID_REQUEST", Error: "invalid output_path"}
	}
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		return response{Status: "DOWNLOAD_FAILED", Error: safeError(err)}
	}
	song := &model.Song{
		ID: input.ID, Name: input.Name, Artist: input.Artist, Album: input.Album,
		AlbumID: input.AlbumID, Duration: input.Duration, Source: "soda",
		Cover: input.Cover, Extra: input.Extra,
	}
	if err := client.Download(song, abs); err != nil {
		return response{Status: "DOWNLOAD_FAILED", Error: safeError(err)}
	}
	info, err := os.Stat(abs)
	if err != nil {
		return response{Status: "DOWNLOAD_FAILED", Error: safeError(err)}
	}
	return response{Status: "DOWNLOADED", Path: abs, Bytes: info.Size()}
}

func safeError(err error) string {
	if err == nil {
		return ""
	}
	message := strings.TrimSpace(err.Error())
	if message == "" {
		message = errors.New("unknown error").Error()
	}
	message = credentialPattern.ReplaceAllString(message, "$1$2[REDACTED]")
	message = urlQueryPattern.ReplaceAllString(message, "$1?[REDACTED]")
	if len(message) > 500 {
		message = message[:500]
	}
	return message
}

func write(value response) {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		_, _ = fmt.Fprintln(os.Stderr, "response encoding failed")
	}
}
