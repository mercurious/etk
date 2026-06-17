import struct, sys, math

src, out = sys.argv[1], sys.argv[2]
data = open(src,'rb').read()
FRAME=132; n=len(data)//FRAME
frames=[list(struct.unpack_from('>i64H', data, i*FRAME)) for i in range(n)]
def col(k): return [f[1+k] for f in frames]

STEER=6
s=col(STEER); m=sum(s)/len(s); s=[x-m for x in s]

# --- segment via per-lap cross-correlation alignment, seeded at P0 ---
P0=3928; W=400; stride=4
tpl=s[0:P0]
def corr(off):
    # correlation of window starting at off vs template
    tot=0.0
    for i in range(0,P0,stride):
        j=off+i
        if 0<=j<n: tot+=tpl[i]*s[j]
    return tot
starts=[0]; 
while True:
    base=starts[-1]+P0
    if base+P0>n: 
        # allow a final partial lap if most of it present
        if base < n-P0*0.5: pass
        break
    best=None
    for off in range(max(0,base-W), min(n-1,base+W)):
        c=corr(off)
        if best is None or c>best[0]: best=(c,off)
    starts.append(best[1])
laps=[st for st in starts if st+P0<=n]
print(f"detected {len(laps)} full laps; starts={laps}")
gaps=[laps[i+1]-laps[i] for i in range(len(laps)-1)]
print(f"lap lengths (frames): {gaps}  mean={sum(gaps)/len(gaps):.0f} ({sum(gaps)/len(gaps)/60:.1f}s)")

# optional 3rd arg K: keep only the K FASTEST laps (shortest = quickest) -> weight toward trained skill
if len(sys.argv)>3:
    K=int(sys.argv[3])
    fastest=sorted(range(len(gaps)), key=lambda i: gaps[i])[:K]; fastest.sort()
    print(f"BEST-{K}: keeping laps idx={fastest} lengths={[gaps[i] for i in fastest]} ({[round(gaps[i]/60,1) for i in fastest]}s)")
    laps=[laps[i] for i in fastest]

# --- cross-lap divergence profile on steering (where did laps disagree?) ---
def phase_vals(k,t): return [frames[st+t][1+k] for st in laps]
div=[]
for t in range(0,P0,30):
    vs=phase_vals(STEER,t); mm=sum(vs)/len(vs); sd=math.sqrt(sum((v-mm)**2 for v in vs)/len(vs)); div.append((sd,t))
div.sort(reverse=True)
avgsd=sum(d for d,_ in div)/len(div)
print(f"\nsteering cross-lap consistency: mean sd={avgsd:.1f} (lower=tighter driving)")
print("  worst-divergence phases (sd, t-frames, t-sec):")
for sd,t in div[:5]: print(f"    sd={sd:6.1f}  t={t:5d}f  {t/60:5.1f}s")

# --- synthesize: avg analog, majority-vote digital, strip mark-chord artifacts ---
ANALOG=set([4,5,6,7]+list(range(8,24)))   # sticks + pressures
DIGI=set([2,3])
CHORD_PRESS={11,17}                        # PRESS_DOWN, PRESS_R1 (mark chord)
D1_STRIP=0x40; D2_STRIP=0x08               # Down (DIG1), R1 (DIG2)
synth=[]
for t in range(P0):
    base=list(frames[laps[0]+t])           # valid frame skeleton (len + structure)
    for k in range(64):
        vs=[frames[st+t][1+k] for st in laps]
        if k in DIGI:
            # majority vote per bit, then strip chord bits
            acc=0
            for b in range(16):
                ones=sum((v>>b)&1 for v in vs)
                if ones*2>len(vs): acc|=(1<<b)
            if k==2: acc&=~D1_STRIP
            if k==3: acc&=~D2_STRIP
            base[1+k]=acc
        elif k in CHORD_PRESS:
            base[1+k]=0                      # drop mark-chord pressure
        elif k in ANALOG:
            base[1+k]=int(round(sum(vs)/len(vs)))
        # else: keep skeleton value
    synth.append(base)

with open(out,'wb') as f:
    for fr in synth:
        f.write(struct.pack('>i64H', fr[0], *fr[1:65]))
print(f"\nwrote {len(synth)} frames -> {out} ({len(synth)*FRAME} bytes, {len(synth)/60:.1f}s consensus lap)")
