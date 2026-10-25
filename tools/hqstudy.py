import json,os,subprocess,urllib.request,urllib.parse,time
OUT="/tmp/out";os.makedirs(OUT,exist_ok=True);R={}
PX="http://127.0.0.1:10809"
def get(u,h=None,px=False,t=25):
 try:
  op=urllib.request.build_opener(urllib.request.ProxyHandler({'http':PX,'https':PX}) if px else urllib.request.ProxyHandler({}))
  r=op.open(urllib.request.Request(u,headers=h or {'User-Agent':'Mozilla/5.0'}),timeout=t);b=r.read();return r.status,b[:300].decode(errors='replace')
 except Exception as e:return 'ERR',str(e)[:200]
R['egress_px']=get('https://ipinfo.io/country',px=True);R['egress']=get('https://ipinfo.io/country')
R['os_legacy']=get('https://rest.opensubtitles.org/search/query-friends/season-1/episode-1/sublanguageid-eng',{'User-Agent':'TemporaryUserAgent','X-User-Agent':'TemporaryUserAgent'})
R['podnapisi']=get('https://www.podnapisi.net/subtitles/search/advanced?keywords=friends&seasons=1&episodes=1&language=en',{'User-Agent':'Mozilla/5.0','Accept':'application/json'})
R['subdl']=get('https://api.subdl.com/api/v1/subtitles?film_name=friends&languages=EN')
R['subsource']=get('https://api.subsource.net/api/searchMovie',{'User-Agent':'Mozilla/5.0'})
R['tvmaze']=get('https://api.tvmaze.com/singlesearch/shows?q=friends')
def run(cmd,t=120):
 try:p=subprocess.run(cmd,capture_output=True,text=True,timeout=t);return p.returncode,(p.stdout[-600:]+' | '+p.stderr[-400:])
 except Exception as e:return 'ERR',str(e)[:200]
Y=[os.path.expanduser('~/.local/bin/yt-dlp')]
R['yt_search']=run(Y+['--flat-playlist','-J','ytsearch3:present perfect tense explained'])
R['yt_search_px']=run(Y+['--proxy',PX,'--flat-playlist','-J','ytsearch3:present perfect tense explained'])
R['yt_subs']=run(Y+['--skip-download','--write-auto-subs','--write-subs','--sub-langs','en.*','--sub-format','vtt','-o','/tmp/v1','https://www.youtube.com/watch?v=jNQXAC9IVRw'])
R['yt_subs_px']=run(Y+['--proxy',PX,'--skip-download','--write-auto-subs','--write-subs','--sub-langs','en.*','--sub-format','vtt','-o','/tmp/v2','https://www.youtube.com/watch?v=jNQXAC9IVRw'])
R['vtt_files']=os.listdir('/tmp')
try:
 from youtube_transcript_api import YouTubeTranscriptApi as A
 try:R['yta']=str(A().fetch('jNQXAC9IVRw'))[:200]
 except Exception as e:R['yta']='ERR '+str(e)[:200]
 try:
  from youtube_transcript_api.proxies import GenericProxyConfig as G
  R['yta_px']=str(A(proxy_config=G(http_url=PX,https_url=PX)).fetch('jNQXAC9IVRw'))[:200]
 except Exception as e:R['yta_px']='ERR '+str(e)[:200]
except Exception as e:R['yta']='import '+str(e)[:100]
json.dump(R,open(OUT+'/probe.json','w'),indent=1);json.dump({'ok':True,'files':[]},open(OUT+'/result.json','w'))
