import struct,sys
src,out,a,b=sys.argv[1],sys.argv[2],int(sys.argv[3]),int(sys.argv[4])
data=open(src,'rb').read(); FRAME=132
frames=data[a*FRAME:b*FRAME]
open(out,'wb').write(frames)
print(f"extracted frames [{a}:{b}] = {b-a} frames ({(b-a)/60:.1f}s) -> {out} ({len(frames)} bytes)")
