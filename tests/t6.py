#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NetEase-like timing: SetAVTransportURI, then Play a little later."""

import os
import re
import threading
import time
import urllib.request
import http.server
import socketserver

HOST = '192.168.3.250'
PORT = 18083
MEDIA = '/tmp/dlnatest'
BASE = 'http://%s:%d' % (HOST, PORT)
TRANSPORT = 'urn:schemas-upnp-org:service:AVTransport:1'
CTRL = {}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=MEDIA, **kw)

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        size = os.path.getsize(path)
        start, end, partial = 0, size - 1, False
        m = re.match(r'bytes=(\d*)-(\d*)', self.headers.get('Range') or '')
        if m:
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = int(m.group(2))
            partial = True
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(length))
        self.end_headers()
        with open(path, 'rb') as f:
            f.seek(start)
            left = length
            while left > 0:
                d = f.read(min(65536, left))
                if not d:
                    break
                self.wfile.write(d)
                left -= len(d)


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
    c, r = soap(TRANSPORT, 'GetTransportInfo', [('InstanceID', 0)])
    if c == 200:
        m = re.search(r'<CurrentTransportState>([^<]*)<', r)
        st = m.group(1) if m else '?'
    pos = '?'
    c, r = soap(TRANSPORT, 'GetPositionInfo', [('InstanceID', 0)])
    if c == 200:
        m = re.search(r'<RelTime>([^<]*)<', r)
        pos = m.group(1) if m else '?'
    try:
        with open('/proc/asound/card0/pcm0p/sub0/status') as f:
            alsa = f.read().split('\n')[0].split(':')[-1].strip()
    except Exception:
        alsa = 'none'
    print('[%6.1fs] %-30s upnp=%-9s pos=%-10s alsa=%s' % (time.time() - t0, tag, st, pos, alsa))


def main():
    serve()
    if not discover():
        print('FAIL: discovery')
        return 1
    t0 = time.time()

    for delay in (0.9, 1.5):
        print('=== SetURI then Play after %.1fs ===' % delay)
        print('  set b ->', set_uri(BASE + '/b.wav')[0])
        time.sleep(delay)
        print('  play  ->', play()[0])
        time.sleep(1.2)
        info(t0, 'play +1.2s (delay %.1f)' % delay)
        time.sleep(1.5)
        info(t0, 'play +2.7s (delay %.1f)' % delay)

    print('=== SetURI while playing, then Play after 1.0s ===')
    print('  set a ->', set_uri(BASE + '/a.wav')[0])
    time.sleep(1.0)
    print('  play  ->', play()[0])
    time.sleep(1.2)
    info(t0, 'after late Play (+1.2s)')
    time.sleep(2.0)
    info(t0, '+3.2s, must keep counting')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
