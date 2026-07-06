#!/bin/bash
# ==========================================================
# ETK PHASE 13.5: HUD BRIDGE (v14.0.0 - MODE-SWITCHED INSTRUMENTS)
# ==========================================================
# AI RULE (HUD spec — strict-lock, two-mode as of the 2026-06-28 moratorium):
# 1. THREE-STAGE TIME-GATED HEADER is locked; the BODY is MODE-SWITCHED via
#    $ETK_HUD_MODE (etk.conf). Stage 1 launch header is identical in both modes.
#    BASIC body : TEMP|LOAD(loadavg)STAT|RAM%STAT|SHADERS    (original)
#    GINSTR body: TEMP|JITTER|SLIP|SHADERS — LOAD+RAM replaced by two live,
#      fixed-width (5-slot) frame-pacing gauges, animated by a per-loop phase,
#      read from the MangoHud autolog ($SHM_DIR/mangolog):
#        JITTER = jitter direction/flow; » settling-forward (improving),
#                 « degrading-back; arrow count = velocity; arrows MARQUEE across
#                 the 5 slots to show flow. Steady = ·····  Polarity: right=better.
#        SLIP   = pacing-slip severity bar 0..5, filled with + (·····/+····/++···/
#                 +++··/++++·/+++++); at MAX the full bar pulses +++++ <-> ===== by phase.
#    Stages: 1 (0..HOLD) launch header; 2 (HOLD..2HOLD) labelled body; 3 (>=2HOLD) bare body.
# 2. ATOMIC SWAP ONLY: MUST use `echo > tmp && mv tmp $LIVE_STAT`
# 3. RAW TEXT ONLY: No MangoHud configuration keys (e.g., custom_text=) in the output.
# 4. The strict-lock (no decorative bloat, dense punctuation DDU) applies to BOTH bodies.
# ==========================================================
source /storage/games-internal/roms/etk/scripts/env.sh

# 0. SHM REBOOT SEEDING
mkdir -p "$SHM_DIR" 2>/dev/null
# G-INSTR: MangoHud's autostart_log (config/MangoHud.conf) writes the per-session
# fps/frametime CSV here. Seed the dir so MangoHud's logger never races a missing
# folder at swapchain init; session_postmortem.sh parses + prunes it on exit.
mkdir -p "$SHM_DIR/mangolog" 2>/dev/null
echo "INITIALIZING" > "$LIVE_STAT"

# Animation phase for the GINSTR gauges (advances each loop; drives the JITTER
# marquee + the SLIP max-pulse). Tested ONCE: does BusyBox `sleep` take fractional
# seconds? A failing in-loop `sleep 0.5` would spin the loop at 100% CPU, so we
# fall back to 1s if not. (GINSTR animates at 0.5s; BASIC stays at 1s.)
TICK=0
if sleep 0.2 2>/dev/null; then FRAC_SLEEP_OK=1; else FRAC_SLEEP_OK=0; fi

# RESCUE FLASH state (2026-07-06, operator: "the system really works, although
# it can be jarring if you don't know what's happening"). The kernel keepalive
# absorbs a GPU hang invisibly — the emulator usually never even sees it (the
# fence re-signals under the emulator-side warn threshold), so the RPCS3
# overlay can't announce it. THIS layer can: the survive line is in dmesg the
# moment it happens, the bridge keeps ticking through the freeze, and MangoHud
# repaints the HUD at the FIRST recovered frame — exactly when the driver
# needs "keep your hands on the wheel". Baseline the counter at launch so
# earlier survives this boot don't flash on session start.
SURV_SEEN=$(dmesg 2>/dev/null | grep -c 'context_keepalive: surviving hang')
case "$SURV_SEEN" in ''|*[!0-9]*) SURV_SEEN=0 ;; esac
RESCUE_TTL=0

while true; do
    # 1. IDENTIFICATION
    source /storage/games-internal/roms/etk/scripts/env.sh
    V_DIR="$VAULT_DIR"
    TICK=$(( (TICK + 1) % 100000 ))    # GINSTR animation phase (marquee + pulse)
    
    # 2. CONSUME THERMAL STATE (Produced by thermal_d.sh)
    T_STAT=$(cat "$SHM_DIR/thermal_stat" 2>/dev/null || echo "WAIT")

    # 3. PERFORMANCE METRICS (BusyBox Safe)
    LOAD_RAW=$(cat /proc/loadavg | awk '{print $1}')
    LOAD_INT=$(echo "$LOAD_RAW" | awk '{print int($1 * 100)}')
    
    L_STAT="»--"
    if [ "$LOAD_INT" -gt 860 ]; then 
        L_STAT="»»»"
    elif [ "$LOAD_INT" -gt 590 ]; then 
        L_STAT="»»-"
    fi
    
    # RAM Calculation and Thresholds
    RAM_VAL=$(free | awk '/Mem:/ {printf("%.0f", $3/$2 * 100)}')
    R_STAT="»--"
    if [ "$RAM_VAL" -ge 90 ]; then
        R_STAT="»»»"
    elif [ "$RAM_VAL" -ge 75 ]; then
        R_STAT="»»-"
    fi

    # 4. ADVANCED SHADER TELEMETRY
    BANK=$(cat "$VAULT_COUNT" 2>/dev/null || echo "0")
    NEW_SHADERS=$(cat "$SHM_DIR/vault_new.txt" 2>/dev/null || echo "0")

    # Shader-count abbreviation to save HUD width (integer math, BusyBox-safe):
    #   < 1000          -> plain count          (e.g. 50)
    #   1000 .. 99999   -> X.Xk, truncated      (e.g. 2549 -> 2.5k)
    #   >= 100000       -> XXXk, truncated      (e.g. 399812 -> 399k)
    if [ "$BANK" -ge 100000 ] 2>/dev/null; then
        BANK_STR="$((BANK / 1000))k"
    elif [ "$BANK" -ge 1000 ] 2>/dev/null; then
        BANK_STR="$((BANK / 1000)).$(( (BANK % 1000) / 100 ))k"
    else
        BANK_STR="$BANK"
    fi
# ==================================================================
# PATCHED VIA PIT WALL: BUSYBOX-SAFE SYMLINK FOOTPRINT CALCULATION
# ==================================================================
# 1. Use -k (Kilobytes) which is universally supported by BusyBox
# 2. Use -L to explicitly FORCE du to follow the vault symlinks
	V_SIZE_KB=$(du -skL "$V_DIR" 2>/dev/null | awk '{print $1}')    
    if [ -z "$V_SIZE_KB" ] || [ "$V_SIZE_KB" -eq 0 ]; then
        # Startup race, not a fault: at emu fire-up the Sentry hasn't pinned the
        # game and vault_d hasn't populated the vault dir yet, so this du reads 0
        # for a few seconds before the vault resolves. Show LOADING, not ERROR —
        # a LOADING that never clears is itself the real-trouble signal.
        VAULT_STR="VAULT:LOADING"
    else
    	# Convert Kilobytes to Megabytes safely using integer arithmetic
    	V_SIZE=$((V_SIZE_KB / 1024))
        # Vault-size abbreviation to keep the HUD trim — mirrors the shader-count
        # k-abbreviation above (a big game means a big vault; GT6's tops 1GB):
        #   < 1024 MB   -> XXMB        (e.g. 690MB)
        #   >= 1024 MB  -> X.XGB       (truncated, e.g. 1228MB -> 1.1GB)
        if [ "$V_SIZE" -ge 1024 ] 2>/dev/null; then
            V_SIZE_STR="$((V_SIZE / 1024)).$(( (V_SIZE % 1024) * 10 / 1024 ))GB"
        else
            V_SIZE_STR="${V_SIZE}MB"
        fi
        VAULT_STR="${NEW_SHADERS}+ ${BANK_STR} ${V_SIZE_STR}"
    fi

    # --- TIME-GATED LAUNCH HEADER (three stages) ---
    # Stage 1 (0..HOLD):     MODE|GAMEID| + telemetry body
    # Stage 2 (HOLD..2HOLD): labeled telemetry body (TEMP:/CORES:/MEM:/SHDRS:)
    # Stage 3 (2HOLD..):     pure telemetry body
    # Clock is the ignition-seeded session_start.txt (install.sh) — no second
    # timer. A missing/non-numeric clock collapses to stage 3.
    STAGE=3
    S_START=$(cat "$SHM_DIR/session_start.txt" 2>/dev/null)
    case "$S_START" in ''|*[!0-9]*) S_START=0 ;; esac
    if [ "$S_START" -gt 0 ] && [ "$TARGET_ID" != "IDLE" ]; then
        AGE=$(( $(date +%s) - S_START ))
        HOLD="${HUD_HEADER_HOLD_S:-60}"
        if [ "$AGE" -ge 0 ] && [ "$AGE" -lt "$HOLD" ]; then
            STAGE=1
        elif [ "$AGE" -ge "$HOLD" ] && [ "$AGE" -lt $(( HOLD * 2 )) ]; then
            STAGE=2
        fi
    fi

    # 4.5 G-INSTR GAUGES (only computed in GINSTR HUD mode)
    # Replace LOAD+RAM with two fixed-width (5-slot) frame-pacing instruments read
    # live from the MangoHud autolog ($SHM_DIR/mangolog; col1=fps, col2=frametime
    # ms, 3-line preamble; race-gated to frametime >= 25ms so menu/transition
    # frames don't contaminate). Both animate by the per-loop TICK phase:
    #   JITTER = direction/flow over a recent-vs-older window. » = settling-forward
    #            (improving), « = degrading-back; arrow COUNT = velocity; the arrows
    #            MARQUEE across the 5 slots (rotate by phase) to show flow. Steady=·····
    #   SLIP   = pacing-slip severity bar 0..5 from mean |Δframetime|, filled with +
    #            (·····/+····/++···/+++··/++++·/+++++); at MAX the bar pulses +++++ <-> =====.
    # All BEGIN thresholds + the gauge width are FEEL-TUNED ON THE RIG.
    HUD_MODE="${ETK_HUD_MODE:-BASIC}"
    if [ "$HUD_MODE" = "GINSTR" ]; then
        G_CSV=$(ls -1t "$SHM_DIR/mangolog"/*.csv 2>/dev/null | head -1)
        if [ -n "$G_CSV" ] && [ -f "$G_CSV" ]; then
            G_OUT=$(awk -F, -v phase="$TICK" '
                BEGIN{ J1=4; J2=8; J3=14; J4=22; J5=32;          # SLIP rung thresholds (mean|Δft| ms) TUNE
                       T1=0.8; T2=2.0; T3=4.0; T4=8.0; T5=14.0;   # JITTER velocity steps (ms trend)    TUNE
                       W=10; SLOTS=5 }                             # sample window ; gauge width         TUNE
                NR>3 && ($1+0)>0 && ($2+0)>=25 { ft[++n]=$2+0 }
                END{
                    s0=(n>W)?n-W+1:1; m=0
                    for(i=s0+1;i<=n;i++){ d=ft[i]-ft[i-1]; if(d<0)d=-d; dd[++m]=d }
                    pad=""; for(i=0;i<SLOTS;i++) pad=pad"·"
                    if(m<2){ printf "%s %s", pad, pad; exit }   # menus / too few race frames
                    s=0; for(i=1;i<=m;i++) s+=dd[i]; jit=s/m
                    h=int(m/2); so=0; sr=0
                    for(i=1;i<=h;i++) so+=dd[i]; for(i=h+1;i<=m;i++) sr+=dd[i]
                    older=(h>0)?so/h:0; recent=((m-h)>0)?sr/(m-h):0; tr=recent-older
                    # JITTER: count=velocity (0..SLOTS); »=improving(tr<0)/«=degrading; marquee by phase
                    at=(tr<0)?-tr:tr; c=0; if(at>=T1)c=1; if(at>=T2)c=2; if(at>=T3)c=3; if(at>=T4)c=4; if(at>=T5)c=5
                    if(c==0){ jg=pad }
                    else{
                        ar=(tr<0)?"»":"«"; sh=(tr<0)?phase:-phase; jg=""
                        for(i=0;i<SLOTS;i++){ j=((i-sh)%SLOTS+SLOTS)%SLOTS; jg=jg ((j<c)?ar:"·") }
                    }
                    # SLIP: level 0..SLOTS by jit; fill + ; at MAX pulse +++++/===== by phase
                    L=0; if(jit>=J1)L=1; if(jit>=J2)L=2; if(jit>=J3)L=3; if(jit>=J4)L=4; if(jit>=J5)L=5
                    if(L>=SLOTS){ sg=(phase%2==0)?"+++++":"=====" }
                    else{ sg=""; for(i=0;i<SLOTS;i++) sg=sg ((i<L)?"+":"·") }
                    printf "%s %s", jg, sg
                }' "$G_CSV")
        else
            G_OUT="····· ·····"
        fi
        JIT="${G_OUT%% *}"; SLIP="${G_OUT##* }"
    fi

    # 4.7 RESCUE GAUGE (2026-07-06, operator-corrected same day: the full-body
    # takeover was obnoxious and against the HUD format principles). RESCUE is
    # a TRANSIENT THIRD GAUGE slot — |JITTER|SLIP|»RESCUE«|VAULT| — present
    # only while a fresh keepalive survive is being announced, then GONE; the
    # segment appearing/disappearing is itself the eye-catcher. Dense Latin-1,
    # no gauge is displaced, strict-lock intact. Pulses »RESCUE«/«RESCUE» by
    # phase while shown.
    CUR_SURV=$(dmesg 2>/dev/null | grep -c 'context_keepalive: surviving hang')
    case "$CUR_SURV" in ''|*[!0-9]*) CUR_SURV=0 ;; esac
    if [ "$CUR_SURV" -gt "$SURV_SEEN" ]; then
        SURV_SEEN=$CUR_SURV
        if [ "$HUD_MODE" = "GINSTR" ] && [ "$FRAC_SLEEP_OK" = "1" ]; then
            RESCUE_TTL=16    # 0.5s ticks (~8s on screen)
        else
            RESCUE_TTL=8     # 1s ticks
        fi
    fi
    RESCUE_SEG=""
    if [ "$RESCUE_TTL" -gt 0 ]; then
        RESCUE_TTL=$((RESCUE_TTL - 1))
        if [ $((TICK % 2)) -eq 0 ]; then
            RESCUE_SEG="»RESCUE«|"
        else
            RESCUE_SEG="«RESCUE»|"
        fi
    fi

    # 5. ATOMIC HUD INJECTION [MANIFEST RULE: HUD FORMAT]
    case "$STAGE" in
        1) FINAL_STRING="${ETK_BUILD_TYPE}|${TARGET_ID}|${RESCUE_SEG}SHDRS ${VAULT_STR}" ;;
        2) if [ "$HUD_MODE" = "GINSTR" ]; then
               FINAL_STRING="TEMP ${T_STAT}|JITTER ${JIT}|SLIP ${SLIP}|${RESCUE_SEG}"
           else
               FINAL_STRING="TEMP ${T_STAT}|LOAD ${LOAD_RAW}${L_STAT}|RAM ${RAM_VAL}%${R_STAT}|${RESCUE_SEG}"
           fi ;;
        *) if [ "$HUD_MODE" = "GINSTR" ]; then
               FINAL_STRING="${T_STAT}|${JIT}|${SLIP}|${RESCUE_SEG}${VAULT_STR}"
           else
               FINAL_STRING="${T_STAT}|${LOAD_RAW}${L_STAT}|${RAM_VAL}%${R_STAT}|${RESCUE_SEG}${VAULT_STR}"
           fi ;;
    esac
    
    # Write to temp file then move to prevent MangoHud from reading an incomplete file
    echo "$FINAL_STRING" > "${LIVE_STAT}.tmp"
    mv "${LIVE_STAT}.tmp" "$LIVE_STAT"

    # GINSTR animates at 0.5s for a live sweep/pulse (only if BusyBox sleep takes
    # fractional secs — FRAC_SLEEP_OK, tested at startup); BASIC + fallback = 1s.
    if [ "$HUD_MODE" = "GINSTR" ] && [ "$FRAC_SLEEP_OK" = "1" ]; then
        sleep 0.5
    else
        sleep 1
    fi
done