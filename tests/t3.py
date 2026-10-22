#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exercise the three track-switch scenarios against the Marshall renderer."""

import os
import re
import math
import json
import wave
import struct
import threading
import time
import urllib.request
import http.server
import socketserver

DELAY_RATE = 0

HOST = '192.168.3.250'
PORT = 18080
MEDIA = '/tmp/dlnatest'
BASE = 'http://%s:%d' % (HOST, PORT)

CTRL = {}
TRANSPORT = 'urn:schemas-upnp-org:service:AVTransport:1'
RC = 'urn:schemas-upnp-org:service:RenderingControl:1'


def gen_wav(path, secs, freq):
    rate = 44100
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        buf = bytearray()
        for i in range(int(rate * secs)):
            buf += struct.pack('<h', int(12000 * math.sin(2 * math.pi * freq * i / rate)))
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


def serve():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(('0.0.0.0', PORT), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def discover():
    for path in ('/', '/description.xml', '/gmediarender-0.0.1.xml'):
        try:
            xml = urllib.request.urlopen('http://%s:49494%s' % (HOST, path), timeout=5).read().decode('utf-8', 'replace')
        except Exception as e:
            continue
        if 'AVTransport' not in xml:
            continue
        root = 'http://%s:49494' % HOST
        for block in re.findall(r'<service>(.*?)</service>', xml, re.S):
            m = re.search(r'<serviceType>([^<]+)</serviceType>', block)
            c = re.search(r'<controlURL>([^<]+)</controlURL>', block)
            if not (m and c):
                continue
            url = c.group(1).strip()
            if not url.startswith('/'):
                url = '/' + url
            CTRL[m.group(1).strip()] = root + url
        if CTRL:
            return True
    return False


def soap(service, action, args, timeout=8):
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
        r = urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'replace')
        return 200, r
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')
    except Exception as e:
        return -1, str(e)


def set_uri(uri):
    return soap(TRANSPORT, 'SetAVTransportURI', [('InstanceID', 0), ('CurrentURI', uri), ('CurrentURIMetaData', '')])


def play():
    return soap(TRANSPORT, 'Play', [('InstanceID', 0), ('Speed', 1)])


def pause():
    return soap(TRANSPORT, 'Pause', [('InstanceID', 0)])


def stop():
    return soap(TRANSPORT, 'Stop', [('InstanceID', 0)])


def info():
    st = '?'
    code, r = soap(TRANSPORT, 'GetTransportInfo', [('InstanceID', 0)])
    if code == 200:
        m = re.search(r'<CurrentTransportState>([^<]*)<', r)
        if m:
            st = m.group(1)
    pos = dur = '?'
    code, r = soap(TRANSPORT, 'GetPositionInfo', [('InstanceID', 0)])
    if code == 200:
        m = re.search(r'<RelTime>([^<]*)<', r)
        pos = m.group(1) if m else '?'
        m = re.search(r'<TrackDuration>([^<]*)<', r)
        dur = m.group(1) if m else '?'
    alsa = '?'
    try:
        with open('/proc/asound/card0/pcm0p/sub0/status') as f:
            alsa = f.read().split('\n')[0].split(':')[-1].strip()
    except Exception:
        alsa = 'none'
    return st, pos, dur, alsa


def show(tag, t0):
    st, pos, dur, alsa = info()
    print('[%6.1fs] %-22s upnp=%-9s pos=%-10s dur=%-10s alsa=%s' % (time.time() - t0, tag, st, pos, dur, alsa))
    return st, pos, alsa


def main():
    os.makedirs(MEDIA, exist_ok=True)
    a = os.path.join(MEDIA, 'a.wav')
    b = os.path.join(MEDIA, 'b.wav')
    if not os.path.exists(a):
        gen_wav(a, 12, 440)
    if not os.path.exists(b):
        gen_wav(b, 30, 660)
    serve()
    if not discover():
        print('FAIL: could not discover the renderer')
        return 1
    time.sleep(0.3)
    t0 = time.time()

    print('=== scenario 1: play A, single switch to B while PLAYING ===')
    print('  set A ->', set_uri(BASE + '/a.wav')[0])
    print('  play  ->', play()[0])
    time.sleep(3)
    show('A playing', t0)
    print('  set B ->', set_uri(BASE + '/b.wav')[0])
    time.sleep(2.5)
    show('after switch to B', t0)
    time.sleep(2)
    show('B +2s', t0)

    print('=== scenario 2: direct skip without pause, B -> A ===')
    print('  set A ->', set_uri(BASE + '/a.wav')[0])
    time.sleep(2.5)
    show('after switch to A', t0)
    time.sleep(1.5)
    show('A +1.5s', t0)

    print('=== scenario 3: burst, three SetURI 30ms apart ===')
    for i, f in enumerate(['/b.wav', '/a.wav', '/b.wav']):
        code = set_uri(BASE + f)[0]
        print('  set %-8s -> %s (t=%.2fs)' % (f, code, time.time() - t0))
        time.sleep(0.03)
    for k in range(4):
        time.sleep(2)
        show('burst +%ds' % (2 * (k + 1)), t0)

    print('=== scenario 4: pause, then switch, then play ===')
    print('  pause ->', pause()[0])
    time.sleep(1)
    show('paused', t0)
    print('  set A ->', set_uri(BASE + '/a.wav')[0])
    time.sleep(2)
    show('switched while paused', t0)
    print('  play  ->', play()[0])
    time.sleep(2)
    show('playing again', t0)
    time.sleep(2)
    show('+2s', t0)

    print('=== scenario 5: rapid 5 switches 100ms apart ===')
    for f in ['/a.wav', '/b.wav', '/a.wav', '/b.wav', '/a.wav']:
        set_uri(BASE + f)
        time.sleep(0.1)
    for k in range(3):
        time.sleep(2)
        show('rapid +%ds' % (2 * (k + 1)), t0)

    print('  stop  ->', stop()[0])
    time.sleep(1)
    show('stopped', t0)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
