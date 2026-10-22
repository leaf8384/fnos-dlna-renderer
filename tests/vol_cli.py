import re, sys, urllib.request
HOST='192.168.3.250'; RC='urn:schemas-upnp-org:service:RenderingControl:1'
CTRL={}
def discover():
    xml=urllib.request.urlopen('http://%s:49494/description.xml'%HOST,timeout=5).read().decode()
    for b in re.findall(r'<service>(.*?)</service>', xml, re.S):
        m=re.search(r'<serviceType>([^<]+)</serviceType>',b); c=re.search(r'<controlURL>([^<]+)</controlURL>',b)
        if m and c: CTRL[m.group(1).strip()]='http://%s:49494/%s'%(HOST,c.group(1).strip().lstrip('/'))
def soap(action,args):
    body=('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:%s xmlns:u="%s">%s</u:%s></s:Body></s:Envelope>')%(action,RC,''.join('<%s>%s</%s>'%(k,v,k) for k,v in args),action)
    r=urllib.request.Request(CTRL[RC],data=body.encode(),headers={'Content-Type':'text/xml; charset="utf-8"','SOAPAction':'"%s#%s"'%(RC,action)})
    try: return urllib.request.urlopen(r,timeout=8).read().decode()
    except urllib.error.HTTPError as e: return 'ERR %s'%e.code
discover()
if sys.argv[1]=='set':
    print('SetVolume', sys.argv[2], '->', 'ok' if 'ERR' not in soap('SetVolume',[('InstanceID',0),('Channel','Master'),('DesiredVolume',sys.argv[2])]) else 'fail')
m=soap('GetVolume',[('InstanceID',0),('Channel','Master')])
print('GetVolume ->', re.search(r'<CurrentVolume>([^<]*)<', m).group(1) if 'CurrentVolume' in m else m[:120])
