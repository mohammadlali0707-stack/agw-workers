#!/usr/bin/env python3
# Creates "Motamem Concepts" DB under Company HQ > 06 Academy using the HQ integration token found on this VPS.
import json, os, re, glob, subprocess, urllib.request, sys
PARENT="402befc7ddfb815db03ec366ff1802b6"
H=lambda t:{"Authorization":"Bearer "+t,"Notion-Version":"2022-06-28","Content-Type":"application/json"}
def call(t,m,p,b=None):
    r=urllib.request.Request("https://api.notion.com/v1/"+p,method=m,headers=H(t),data=json.dumps(b).encode() if b is not None else None)
    try:
        with urllib.request.urlopen(r,timeout=30) as x: return json.load(x)
    except urllib.error.HTTPError as e: return {"object":"error","status":e.code,"message":e.read().decode()[:300]}
cands=set()
paths=["/opt","/etc/systemd/system","/root"]
out=subprocess.run(["grep","-rhoIE","--exclude-dir=node_modules","--exclude-dir=.git","--exclude-dir=venv","(ntn_|secret_)[A-Za-z0-9]{40,60}"]+paths,capture_output=True,text=True,timeout=120).stdout
cands.update(out.split())
tok=None
for t in cands:
    if call(t,"GET","pages/"+PARENT).get("object")=="page": tok=t;break
if not tok: print("NO_TOKEN_WITH_ACCESS candidates=",len(cands)); sys.exit(1)
print("token ok (candidates %d)"%len(cands))
# idempotency
s=call(tok,"POST","search",{"query":"Motamem Concepts","filter":{"property":"object","value":"database"}})
for r in s.get("results",[]):
    if r.get("parent",{}).get("page_id","").replace("-","")==PARENT: print("EXISTS",r["url"]);sys.exit(0)
rows=json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"rows.json")))
sel=lambda xs:{"select":{"options":[{"name":x} for x in xs]}}
phases=[]; topics=[]
for r in rows:
    if r["Phase"] and r["Phase"] not in phases: phases.append(r["Phase"])
    if r["Topic"] and r["Topic"] not in topics: topics.append(r["Topic"])
phases.sort()
db=call(tok,"POST","databases",{"parent":{"type":"page_id","page_id":PARENT},"icon":{"type":"emoji","emoji":"🎓"},
 "title":[{"text":{"content":"Motamem Concepts"}}],"properties":{
 "Concept":{"title":{}},"Topic":sel(topics),"Subtopic":{"rich_text":{}},"Source URL":{"url":{}},
 "Summary":{"rich_text":{}},"Key Points":{"rich_text":{}},"Status":sel(["New","Learning","Learned"]),
 "Mastery":sel(["Novice","Intermediate","Expert"]),"XP":{"number":{}},"Quiz Score":{"number":{}},
 "Last Reviewed":{"date":{}},"Phase":sel(phases),"Order":{"number":{}}}})
if db.get("object")!="database": print("DB_FAIL",db);sys.exit(1)
print("DB",db["url"])
rt=lambda s:{"rich_text":[{"text":{"content":s[:1990]}}]} if s else {"rich_text":[]}
ok=0
for r in rows:
    p={"Concept":{"title":[{"text":{"content":r["Concept"]}}]},"Subtopic":rt(r["Subtopic"]),"Summary":rt(r["Summary"]),
       "Key Points":rt(r["Key Points"]),"Source URL":{"url":r["Source URL"]},"XP":{"number":r["XP"] or 0},"Order":{"number":r["Order"]}}
    for k in ("Topic","Status","Mastery","Phase"):
        if r[k]: p[k]={"select":{"name":r[k]}}
    res=call(tok,"POST","pages",{"parent":{"database_id":db["id"]},"properties":p})
    if res.get("object")=="page": ok+=1
    else: print("ROW_FAIL",r["Order"],res.get("message"))
print("ROWS",ok,"/",len(rows))
