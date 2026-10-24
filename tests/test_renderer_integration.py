#!/usr/bin/env python3
"""Isolated real GStreamer/UPnP regression: alternate port + ALSA null.

No changes to the installed renderer, its volume file, or physical speakers.
python3 tests/test_renderer_integration.py --renderer build/.../src/gmediarender
"""
import argparse, array, math, os, re, subprocess, tempfile, time, urllib.request, wave
from pathlib import Path
from xml.sax.saxutils import escape

args=argparse.ArgumentParser()
args.add_argument('--renderer',required=True)
args.add_argument('--host',default='192.168.3.250')
args.add_argument('--interface',default='enp5s0')
args.add_argument('--port',type=int,default=49610)
a=args.parse_args()
binary=str(Path(a.renderer).resolve())
base=f'http://{a.host}:{a.port}'

def soap(action,body='',rendering=False):
    kind='RenderingControl' if rendering else 'AVTransport'
    ns=f'urn:schemas-upnp-org:service:{kind}:1'
    path='rendercontrol1' if rendering else 'rendertransport1'
    xml=f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:{action} xmlns:u="{ns}"><InstanceID>0</InstanceID>{body}</u:{action}></s:Body></s:Envelope>'
    req=urllib.request.Request(base+'/upnp/control/'+path,data=xml.encode(),headers={'Content-Type':'text/xml','SOAPACTION':f'"{ns}#{action}"'})
    return urllib.request.urlopen(req,timeout=5).read().decode()

def volume(n): return soap('SetVolume',f'<Channel>Master</Channel><DesiredVolume>{n}</DesiredVolume>',True)
def mute(n): return soap('SetMute',f'<Channel>Master</Channel><DesiredMute>{n}</DesiredMute>',True)

with tempfile.TemporaryDirectory(prefix='dlna-integration-') as folder:
    d=Path(folder)
    for name in ['a','b']:
        with wave.open(str(d/(name+'.wav')),'wb') as w:
            w.setnchannels(2);w.setsampwidth(2);w.setframerate(44100);w.writeframes(b'\0'*44100*4*30)
    (d/'volume.state').write_text('50\n')
    # Only the independent beep uses this PCM. Music goes to fakesink, so
    # music cannot make the signal-energy assertions pass accidentally.
    (d/'asound.conf').write_text('</usr/share/alsa/alsa.conf>\npcm.codex_beep {\n type file\n slave.pcm "null"\n file "'+str(d/'beep.raw')+'"\n format "raw"\n}\n')
    env=dict(os.environ,GMR_VOLUME_STATE=str(d/'volume.state'),LANG='C.UTF-8',ALSA_CONFIG_PATH=str(d/'asound.conf'))
    def uri(name):
        soap('SetAVTransportURI',f'<CurrentURI>{escape((d/(name+".wav")).as_uri())}</CurrentURI><CurrentURIMetaData></CurrentURIMetaData>')
    with (d/'renderer.log').open('w') as output:
        process=subprocess.Popen([binary,'-I',a.interface,'-p',str(a.port),'-u','codex-regression-1110','-f','DLNA regression (silent)','--gstout-audiopipe=fakesink sync=true','--gstout-audiodevice=codex_beep','--logfile=stdout'],stdout=output,stderr=subprocess.STDOUT,env=env)
        try:
            for _ in range(50):
                if process.poll() is not None: raise RuntimeError('test renderer exited')
                try: urllib.request.urlopen(base+'/description.xml',timeout=.3).close();break
                except OSError: time.sleep(.1)
            uri('a');soap('Play','<Speed>1</Speed>');time.sleep(1.5)
            assert 'PLAYING' in soap('GetTransportInfo')
            volume(80)
            assert '<CurrentVolume>80</CurrentVolume>' in soap('GetVolume','<Channel>Master</Channel>',True)
            time.sleep(1)
            log=(d/'renderer.log').read_text()
            assert log.index('Volume tone first:')<log.index('Volume ramp after tone:')<log.index('Volume applied:')
            # The ALSA null slave can emit a large amount of zero padding.
            # Analyze only 200ms around the actual signal, without spending
            # longer than the test track's duration processing silence.
            left=[]
            with (d/'beep.raw').open('rb') as capture:
                while block:=capture.read(8192):
                    raw=array.array('h');raw.frombytes(block)
                    channel=raw[::2]
                    if any(channel):
                        first=next(i for i,v in enumerate(channel) if v)
                        signal=list(channel[first:])
                        more=array.array('h');more.frombytes(capture.read(max(0,8820-len(signal))*4))
                        left=[v/32768 for v in (signal+list(more[::2]))[:8820]]
                        break
            assert left,'beep pipeline emitted only silence'
            signal_end=max(i for i,v in enumerate(left) if v)
            signal_seconds=signal_end/44100
            assert .11 < signal_seconds < .13,('incorrect chime duration',signal_seconds)
            peak=max(map(abs,left))
            assert .035 < peak < .06,('beep gain below audible baseline',peak)
            def energy(freq):
                x=sum(v*math.cos(2*math.pi*freq*i/44100) for i,v in enumerate(left))
                y=sum(v*math.sin(2*math.pi*freq*i/44100) for i,v in enumerate(left))
                return x*x+y*y
            off=energy(650)+energy(1700)
            assert energy(882)>10*off and energy(1323)>10*off,'missing chime frequencies'
            print(f'PASS real beep PCM: peak={peak:.4f}, duration={signal_seconds:.3f}s, both chime frequencies present',flush=True)
            print('PASS real pipeline: acknowledgement, ramp, final target; SOAP reports target immediately',flush=True)
            before=len(log);volume(60);time.sleep(.21);volume(40);time.sleep(1)
            tail=(d/'renderer.log').read_text()[before:]
            assert re.search(r'Volume applied:.*level=40',tail)
            applied=re.findall(r'Volume applied:.*level=(\d+)',tail)
            assert applied and applied[-1]=='40'
            print('PASS real pipeline: overlapping requests converge to the latest target',flush=True)
            volume(70);time.sleep(.21);mute(1);time.sleep(.5)
            assert '<CurrentMute>1</CurrentMute>' in soap('GetMute','<Channel>Master</Channel>',True)
            volume(70)
            assert '<CurrentMute>0</CurrentMute>' in soap('GetMute','<Channel>Master</Channel>',True)
            print('PASS real pipeline: explicit mute and same-level unmute',flush=True)
            volume(65);time.sleep(.21);soap('Pause');time.sleep(.6)
            assert 'PAUSED' in soap('GetTransportInfo')
            soap('Play','<Speed>1</Speed>')
            for name in ['b','a','b']:
                soap('Seek','<Unit>REL_TIME</Unit><Target>0:00:00</Target>');uri(name);time.sleep(2)
                assert 'PLAYING' in soap('GetTransportInfo')
                pos=soap('GetPositionInfo');value=re.search(r'<RelTime>(.*?)</RelTime>',pos)
                assert value and value[1] not in ('0:00:00','00:00:00'),pos
            print('PASS real pipeline: pause/resume and three Seek+URI switches',flush=True)
            soap('Stop')
        except BaseException:
            print((d/'renderer.log').read_text()[-16000:])
            raise
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
