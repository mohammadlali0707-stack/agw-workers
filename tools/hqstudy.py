import json,os,subprocess,urllib.request,gzip
OUT="/tmp/out";os.makedirs(OUT,exist_ok=True);R={}
H={'User-Agent':'TemporaryUserAgent','X-User-Agent':'TemporaryUserAgent'}
try:
 s=json.load(urllib.request.urlopen(urllib.request.Request('https://rest.opensubtitles.org/search/query-inception/sublanguageid-eng',headers=H),timeout=30))
 R['n']=len(s);top=sorted(s,key=lambda x:-int(x.get('SubDownloadsCnt') or 0))[0]
 R['top']={k:top.get(k) for k in ('MovieName','MovieYear','IDMovieImdb','SubFormat','SubDownloadsCnt','SubDownloadLink','ZipDownloadLink','SeriesSeason','SeriesEpisode','MovieKind','SubHearingImpaired')}
 try:
  b=urllib.request.urlopen(urllib.request.Request(top['SubDownloadLink'],headers=H),timeout=30).read();R['dl']=gzip.decompress(b)[:300].decode(errors='replace')
 except Exception as e:R['dl']='ERR '+str(e)[:200]
except Exception as e:R['os']='ERR '+str(e)[:200]
Y=[os.path.expanduser('~/.local/bin/yt-dlp')]
for pc in ('tv','mweb','web_safari','android_vr','tv_embedded'):
 try:p=subprocess.run(Y+['--extractor-args','youtube:player_client='+pc,'--skip-download','--write-auto-subs','--sub-langs','en','--sub-format','vtt','-o','/tmp/v_'+pc,'https://www.youtube.com/watch?v=jNQXAC9IVRw'],capture_output=True,text=True,timeout=90);R['yt_'+pc]=[p.returncode,p.stderr[-200:],[f for f in os.listdir('/tmp') if f.startswith('v_'+pc)]]
 except Exception as e:R['yt_'+pc]=str(e)[:100]
json.dump(R,open(OUT+'/probe.json','w'),indent=1);json.dump({'ok':True,'files':[]},open(OUT+'/result.json','w'))
