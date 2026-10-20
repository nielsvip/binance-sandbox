import re,sys
src=open(sys.argv[1]).read().split("\n")
def find(s,start=0):
    for k in range(start,len(src)):
        if src[k]==s: return k
    raise SystemExit("anchor missing: "+s)
a=find("            fire = entry_sig[i]")
b=None
for k in range(a,len(src)):
    if src[k].strip()=="pos = _open(qty0, px, i, entry_reason)": b=k; break
assert b
out=[]
for k,l in enumerate(src):
    L=k+1
    if a<=k<b:
        m=re.match(r"^(\s*)fire = False(.*)$",l)
        if m: l=f"{m.group(1)}fire = _rtv(i, {L}){m.group(2)}"
        m=re.match(r"^(\s*)continue(\s*#.*)?$",l)
        if m: l=f"{m.group(1)}_rtc(i, {L}); continue{m.group(2) or ''}"
        if l=="            _selltop_recross_bypass = False":
            l=l+"\n            if has_closed_before: _RT.append((i, 'pre', bool(fire), _fire_src, bool(entry_sig[i])))"
    if k==b:
        ind=re.match(r"^(\s*)",l).group(1)
        l=l+f"\n{ind}_RT.append((i, 'open', entry_reason, bool(has_closed_before)))"
    out.append(l)
s="\n".join(out)
s=s.replace("        if cd > 0:\n            cd -= 1\n            continue","        if cd > 0:\n            if pos is None and has_closed_before: _RT.append((i, 'cd', cd))\n            cd -= 1\n            continue",1)
s=s.replace("                    _vig_scan_start = len(trades)\n                else:\n                    continue","                    _vig_scan_start = len(trades)\n                else:\n                    _RT.append((i, 'vig_block')); continue",1)
s=s.replace("def simulate_one(npz, sym, is_long, cfg, force_initial_seed=False):","_RT = []\ndef _rtv(i, l):\n    _RT.append((i, 'veto', l))\n    return False\ndef _rtc(i, l):\n    _RT.append((i, 'skip', l))\n\ndef simulate_one(npz, sym, is_long, cfg, force_initial_seed=False):",1)
open(sys.argv[2],"w").write(s)
print("region",a+1,b+1)
