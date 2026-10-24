package main

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestProtectWrites(t *testing.T) {
	for _, tc := range []struct {
		name, content, origin, header, site string
		want                                int
	}{
		{"same origin", "application/json", "http://nas:8790", "1", "same-origin", 204},
		{"native client", "application/json; charset=utf-8", "", "1", "", 204},
		{"cross origin", "application/json", "http://evil.example", "1", "", 403},
		{"simple text request", "text/plain", "", "", "", 403},
		{"form", "application/x-www-form-urlencoded", "", "", "", 403},
		{"no custom header", "application/json", "", "", "", 403},
		{"opaque origin", "application/json", "null", "1", "", 403},
		{"wrong scheme", "application/json", "https://nas:8790", "1", "", 403},
		{"cross-site metadata", "application/json", "", "1", "cross-site", 403},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := httptest.NewRequest("POST", "http://nas:8790/api/config", strings.NewReader(`{}`))
			r.Header.Set("Content-Type", tc.content)
			r.Header.Set("Origin", tc.origin)
			r.Header.Set("X-DLNA-Request", tc.header)
			r.Header.Set("Sec-Fetch-Site", tc.site)
			w := httptest.NewRecorder()
			protectWrites(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) })).ServeHTTP(w, r)
			if w.Code != tc.want {
				t.Fatalf("got %d want %d", w.Code, tc.want)
			}
		})
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestTrackSwitchDiscardsPreviousPosition(t *testing.T) {
	d := t.TempDir()
	p := paths{conf: filepath.Join(d, "conf"), pidFile: filepath.Join(d, "pid")}
	os.WriteFile(p.conf, []byte("IFACE=lo\n"), 0600)
	os.WriteFile(p.pidFile, []byte(fmt.Sprint(os.Getpid())), 0600)
	old := upnpClient
	defer func() { upnpClient = old }()
	upnpClient = &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		body := "<CurrentVolume>70</CurrentVolume>"
		switch {
		case strings.Contains(r.Header.Get("SOAPACTION"), "GetTransportInfo"):
			body = "<CurrentTransportState>PLAYING</CurrentTransportState>"
		case strings.Contains(r.Header.Get("SOAPACTION"), "GetPositionInfo"):
			body = "<TrackURI>old-song</TrackURI><RelTime>0:02:00</RelTime><TrackDuration>0:03:00</TrackDuration>"
		case strings.Contains(r.Header.Get("SOAPACTION"), "GetMediaInfo"):
			body = "<CurrentURI>new-song</CurrentURI>"
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), Header: make(http.Header)}, nil
	})}
	n := nowPlaying(p)
	if n.URI != "new-song" || !n.HasMediaInfo || n.HasPosition {
		t.Fatalf("mixed track snapshot: %+v", n)
	}
}

func TestPartialSnapshotDoesNotInventValues(t *testing.T) {
	d := t.TempDir()
	p := paths{conf: filepath.Join(d, "conf"), pidFile: filepath.Join(d, "pid")}
	os.WriteFile(p.conf, []byte("IFACE=lo\n"), 0600)
	os.WriteFile(p.pidFile, []byte(fmt.Sprint(os.Getpid())), 0600)
	old := upnpClient
	defer func() { upnpClient = old }()
	upnpClient = &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		if strings.Contains(r.Header.Get("SOAPACTION"), "GetTransportInfo") {
			return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader("<CurrentTransportState>PLAYING</CurrentTransportState>")), Header: make(http.Header)}, nil
		}
		return nil, fmt.Errorf("temporary query failure")
	})}
	n := nowPlaying(p)
	if n.State != "PLAYING" || !n.Running || n.HasVolume || n.HasPosition || n.HasMediaInfo {
		t.Fatalf("bad partial snapshot: %+v", n)
	}
	upnpClient = &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) { return nil, fmt.Errorf("offline") })}
	n = nowPlaying(p)
	if n.Error == "" || n.State != "" {
		t.Fatalf("failed query must not claim STOPPED: %+v", n)
	}
}
