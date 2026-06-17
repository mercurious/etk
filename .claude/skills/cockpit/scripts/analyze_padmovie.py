import struct, sys

path = sys.argv[1]
data = open(path,'rb').read()
FRAME = 132
n = len(data)//FRAME
frames = [struct.unpack_from('>i64H', data, i*FRAME) for i in range(n)]
# frame tuple: [0]=len, then [1..64]=button[0..63]; so button[k] = frame[1+k]
def ch(k): return [f[1+k] for f in frames]

print(f"frames={n}  (~{n/60:.1f}s @60Hz)")

# channel activity: stddev
import math
def stats(xs):
    m=sum(xs)/len(xs); v=sum((x-m)**2 for x in xs)/len(xs); return m, math.sqrt(v), min(xs), max(xs)
print("\nactive channels (idx: mean sd min max)  [known: 2=DIG1 3=DIG2 4=RX 5=RY 6=LX 7=LY 8+=press]")
active=[]
for k in range(64):
    xs=ch(k); m,sd,mn,mx=stats(xs)
    if sd>1.0 or (mx-mn)>2:
        active.append((k,sd)); print(f"  {k:2d}: mean={m:7.1f} sd={sd:7.2f} min={mn} max={mx}")

# pick steering = most-active analog in idx 4..7, else most-active overall
cand=[k for k in (6,4,7,5) if any(a==k for a,_ in active)]
steer = cand[0] if cand else max(active,key=lambda t:t[1])[0]
print(f"\nautocorr on channel {steer} (steering guess)")
xs=ch(steer); m=sum(xs)/len(xs); xs=[x-m for x in xs]
# decimate x4 for speed
dec=4; ys=xs[::dec]; N=len(ys)
e0=sum(y*y for y in ys) or 1.0
# search lap period in 30..130s -> frames/dec
lo=int(30*60/dec); hi=int(130*60/dec)
best=[]
for lag in range(lo,hi):
    s=0.0
    for i in range(0,N-lag,3):  # stride 3 for speed
        s+=ys[i]*ys[i+lag]
    best.append((s, lag*dec))
best.sort(reverse=True)
print("  top autocorr lags (frames -> seconds -> implied laps over run):")
seen=[]
for score,lagf in best:
    if all(abs(lagf-p)>120 for p in seen):  # dedup near-peaks
        seen.append(lagf)
        print(f"    lag={lagf:6d}f  {lagf/60:6.2f}s  laps~{n/lagf:.2f}")
    if len(seen)>=6: break
