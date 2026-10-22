#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reproduce the mid-download abort: serve a big file slowly, then switch."""

import os
import re
import math
import wave
import struct
import threading
import time
import urllib.request
import http.server
import socketserver

DELAY_RATE = 0
import subprocess

HOST = '192.168.3.250'
PORT = 18081
MEDIA = '/tmp/dlnatest'
BASE = 'http://%s:%d' % (HOST, PORT)
TRANSPORT = 'urn:schemas-upnp-org:service:AVTransport:1'
CHUNK = 16 * 1024
DELAY = 0.05          # ~320 KB/s: a 3 MB file needs ~10 s


def gen_wav(path, secs, freq):
    rate = 44100
    with wave.open(path, 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        buf = bytearray()
        for i in range(int(rate * secs)):
            v = int(9000 * math.sin(2 * math.pi * freq * i / rate))
            buf += struct.pack('<hh', v, v)
        w.writeframes(bytes(buf))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=MEDIA, **kw)

    def do_GET(self):  # HTTP_RANGE_OK
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get('Range')
        m = re.match(r'bytes=(\d*)-(\d*)', rng or '')
        partial = False
        if m:
            a, b = m.group(1), m.group(2)
            if a:
                start = int(a)
            if b:
                end = int(b)
            elif a:
                end = size - 1
            partial = True
        if start >= size:
            self.send_response(416)
            self.send_header('Content-Range', 'bytes */%d' % size)
            self.end_headers()
            return
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.send_header('Content-Length', str(length))
        self.end_headers()
        remaining = length
        try:
            with open(path, 'rb') as f:
                f.seek(start)
                while remaining > 0:
                    data = f.read(min(32 * 1024, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    self.wfile.flush()
                    remaining -= len(data)
                    time.sleep(DELAY_RATE)
        except Exception:
            pass

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Content-Length', str(size))
        self.end_headers()
        try:
            with open(path, 'rb') as f:
                while True:
                    data = f.read(CHUNK)
                    if not data:
                        break
                    self.wfile.write(data)
                    self.wfile.flush()
                    time.sleep(DELAY)
        except Exception:
            pass


def serve():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(('0.0.0.0', PORT), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


CTRL = {}


def discover():
    xml = urllib.request.urlopen('http://%s:49494/description.xml' % HOST, timeout=5).read().decode('utf-8', 'replace')
    root = 'http://%s:49494' % HOST
    for block in re.findall(r'<service>(.*?)</service>', xml, re.S):
        m = re.search(r'<serviceType>([^<]+)</serviceType>', block)
        c = re.search(r'<controlURL>([^<]+)</controlURL>', block)
        if m and c:
            CTRL[m.group(1).strip()] = root + '/' + c.group(1).strip().lstrip('/')
    return bool(CTRL)


def soap(service, action, args, timeout=10):
    body = ('<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            '<u:%s xmlns:u="%s">%s</u:%s></s:Body></s:Envelope>') % (
                action, service,
                ''.join('<%s>%s</%s>' % (k, v, k) for k, v in args), action)
    req = urllib.request.Request(CTRL[service], data=body.encode('utf-8'),
                                 headers={'Content-Type': 'text/xml; charset="utf-8"',
                                          'SOAPAction': '"%s#%s"' % (service, action)})
    try:
        return 200, urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')
    except Exception as e:
        return -1, str(e)


def set_uri(u):
    return soap(TRANSPORT, 'SetAVTransportURI', [('InstanceID', 0), ('CurrentURI', u), ('CurrentURIMetaData', '')])


def play():
    return soap(TRANSPORT, 'Play', [('InstanceID', 0), ('Speed', 1)])


def info(t0, tag):
    st = '?'
    code, r = soap(TRANSPORT, 'GetTransportInfo', [('InstanceID', 0)])
    if code == 200:
        m = re.search(r'<CurrentTransportState>([^<]*)<', r)
        st = m.group(1) if m else '?'
    pos = dur = '?'
    code, r = soap(TRANSPORT, 'GetPositionInfo', [('InstanceID', 0)])
    if code == 200:
        m = re.search(r'<RelTime>([^<]*)<', r)
        pos = m.group(1) if m else '?'
        m = re.search(r'<TrackDuration>([^<]*)<', r)
        dur = m.group(1) if m else '?'
    try:
        with open('/proc/asound/card0/pcm0p/sub0/status') as f:
            alsa = f.read().split('\n')[0].split(':')[-1].strip()
    except Exception:
        alsa = 'none'
    print('[%6.1fs] %-24s upnp=%-9s pos=%-10s dur=%-10s alsa=%s' % (time.time() - t0, tag, st, pos, dur, alsa))
    return st, pos, alsa


def main():
    os.makedirs(MEDIA, exist_ok=True)
    big = os.path.join(MEDIA, 'big1.wav')
    big2 = os.path.join(MEDIA, 'big2.wav')
    if not os.path.exists(big):
        gen_wav(big, 200, 330)
    if not os.path.exists(big2):
        gen_wav(big2, 200, 550)
    print('file size: %.1f MB' % (os.path.getsize(big) / 1048576.0))
    serve()
    if not discover():
        print('FAIL: discovery')
        return 1
    t0 = time.time()

    print('=== A: switch while the stream is still downloading ===')
    print('  set big1 ->', set_uri(BASE + '/big1.wav')[0])
    print('  play     ->', play()[0])
    time.sleep(3)
    info(t0, 'big1 downloading')
    for i in range(4):
        print('  set big2 #%d -> %s' % (i + 1, set_uri(BASE + '/big2.wav')[0]))
        time.sleep(3)
        info(t0, 'big2 after burst %d' % (i + 1))

    print('=== B: alternate between two slow streams quickly ===')
    for i in range(6):
        f = '/big1.wav' if i % 2 == 0 else '/big2.wav'
        set_uri(BASE + f)
        time.sleep(0.4)
        info(t0, 'alt %d %s' % (i + 1, f))
    for k in range(3):
        time.sleep(2)
        info(t0, 'settle +%ds' % (2 * (k + 1)))

    print('=== C: back to a fast local file ===')
    set_uri(BASE + '/a.wav')
    time.sleep(2.5)
    info(t0, 'fast file')
    time.sleep(2)
    info(t0, 'fast +2s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
