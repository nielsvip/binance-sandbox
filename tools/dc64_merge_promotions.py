import json,os
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
res=[]
for p in ("dc64_promotions_crypto.json","dc64_promotions_stocks.json"):
    fp=os.path.join(ROOT,"data","reports",p)
    if os.path.exists(fp):
        try: res+=json.load(open(fp)).get("results",[])
        except Exception: pass
out=os.path.join(ROOT,"data","reports","dc64_promotions.json")
json.dump({"n_pass":sum(1 for r in res if r.get("pass")),"n_total":len(res),"results":res},open(out,"w"),indent=1)
print("merged",len(res),"->",out)
