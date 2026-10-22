#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-of-stream then Play again: the track must restart, not stay silent."""

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

HOST = '192.168.3.250'
PORT = 18082
MEDIA = '/tmp/dlnatest'
BASE = 'http://%s:%d' % (HOST, PORT)
TRANSPORT = 'urn:schemas-upnp-org:service:AVTransport:1'
CTRL = {}


def gen_wav(path, secs, freq):
    rate = 44100
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        buf = bytearray()
        for i in range(int(rate * secs)):
            buf += struct.pack('<h', int(11000 * math.sin(2 * math.pi * freq * i / rate)))
        w.writeframes(bytes(buf))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=MEDIA, **kw)

    def log_message(self, *a):
        pass


def serve():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(('0.0.0.0', PORT), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def discover():
    xml = urllib.request.urlopen('http://%s:49494/description.xml' % HOST, timeout=5).read().decode('utf-8', 'replace')
    root = 'http://%s:49494' % HOST
    for block in re.findall(r'<service>(.*?)</service>', xml, re.S):
        m = re.search(r'<serviceType>([^<]+)</serviceType>', block)
        c = re.search(r'<controlURL>([^<]+)</controlURL>', block)
        if m and c:
            CTRL[m.group(1).strip()] = root + '/' + c.group(1).strip().lstrip('/')
    return bool(CTRL)


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
    pos = '?'
    code, r = soap(TRANSPORT, 'GetPositionInfo', [('InstanceID', 0)])
    if code == 200:
        m = re.search(r'<RelTime>([^<]*)<', r)
        pos = m.group(1) if m else '?'
    try:
        with open('/proc/asound/card0/pcm0p/sub0/status') as f:
            alsa = f.read().split('\n')[0].split(':')[-1].strip()
    except Exception:
        alsa = 'none'
    print('[%6.1fs] %-26s upnp=%-9s pos=%-10s alsa=%s' % (time.time() - t0, tag, st, pos, alsa))
    return st, pos, alsa


def main():
    os.makedirs(MEDIA, exist_ok=True)
    short = os.path.join(MEDIA, 'short.wav')
    if not os.path.exists(short):
        gen_wav(short, 3, 880)
    serve()
    if not discover():
        print('FAIL: discovery')
        return 1
    t0 = time.time()

    print('=== EOS then Play again ===')
    print('  set short ->', set_uri(BASE + '/short.wav')[0])
    print('  play      ->', play()[0])
    time.sleep(1.5)
    info(t0, 'playing 3s clip')
    time.sleep(3.5)
    info(t0, 'after EOS')
    print('  play again->', play()[0])
    time.sleep(1.5)
    info(t0, 'replayed')
    time.sleep(1.5)
    info(t0, 'replayed +1.5s')

    print('=== Play twice in a row (idempotent, must not rewind) ===')
    print('  set a   ->', set_uri(BASE + '/a.wav')[0])
    print('  play    ->', play()[0])
    time.sleep(3)
    info(t0, 'a playing 3s')
    print('  play#2  ->', play()[0])
    time.sleep(1.0)
    info(t0, 'after redundant Play')
    time.sleep(2)
    info(t0, '+2s (must keep counting)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
