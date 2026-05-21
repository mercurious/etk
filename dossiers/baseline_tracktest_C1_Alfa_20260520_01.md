# Track Test
May 20
## Test Cleanboot C-1 Alfa Baseline 
### Notes
- driver delay: 50
- BATT at 50% at start of stint

### Test Results

1.  Freeze in-race, toward end of Lap 1
`SM8250:~ # dmesg | strings | grep -iE "adreno|kgsl|turnip|fence|timeout|msm_dpu|panic|oom|killed process|segfault|sigsegv|hard lockup|watchdog|call trace|backtrace|firmware|out of memory|rcu_sched" | tail -n 30
[    0.000000] psci: PSCIv1.1 detected in firmware.
[    0.069235] Call trace:
[    0.692779] qcom_scm firmware:scm: qseecom: found qseecom with version 0x1402000
[    0.692796] qcom_scm firmware:scm: qseecom: untested machine, skipping
[    1.254090] msm_dpu ae01000.display-controller: bound ae94000.dsi (ops 0xffff800081326b38)
[    1.255302] msm_dpu ae01000.display-controller: bound ae90000.displayport-controller (ops 0xffff8000813256b8)
[    1.255480] adreno 3d00000.gpu: supply vdd not found, using dummy regulator
[    1.255501] adreno 3d00000.gpu: supply vddcx not found, using dummy regulator
[    1.263807] msm_dpu ae01000.display-controller: bound 3d00000.gpu (ops 0xffff8000812f2538)
[    1.266547] msm_dpu ae01000.display-controller: [drm:adreno_request_fw] loaded qcom/a650_sqe.fw from new location
[    1.266555] msm_dpu ae01000.display-controller: [drm:adreno_request_fw] loaded qcom/a650_gmu.bin from new location
[    1.267131] [drm] Loaded GMU firmware v2.1.8
[    1.397327] msm_dpu ae01000.display-controller: [drm] fb0: msmdrmfb frame buffer device
[    1.399949] faux_driver regulatory: Direct firmware load for regulatory.db failed with error -2
[    2.665734] kernel-overlays-setup: added firmware from /usr/lib/kernel-overlays/base/lib/firmware
[    4.871782] Call trace:
[  298.192191] adreno 3d00000.gpu: [drm:a6xx_irq] *ERROR* gpu fault ring 0 fence 594b status 00800005 rb 0683/0683 ib1 0000000157C71000/12d2 ib2 00000001ABF03714/0000
[  298.192241] msm_dpu ae01000.display-controller: [drm:recover_worker] *ERROR* 6.5.0.2: hangcheck recover!
[  298.192261] msm_dpu ae01000.display-controller: [drm:recover_worker] *ERROR* 6.5.0.2: offending task: RSX Offloader (/tmp/.mount_eeOgt85l/AppRun.wrapped --no-gui /storage/.config/rpcs3/dev_hdd0/game/NPUA80075/USRDIR/EBOOT.BIN)
[  298.192277] rb 0: fence:    22856/22859
SM8250:~ # `

2. Clean Gold. 79C 8.43 94% NEW:0 at main menu after save

3. BATT: 43%. at start. BATT 39% at freeze HUD: 62C 10.82 94% NEW:0
- Freeze in lap 2 tunnel

`SM8250:~ # dmesg | strings | grep -iE "adreno|kgsl|turnip|fence|timeout|msm_dpu|panic|oom|killed process|segfault|sigsegv|hard lockup|watchdog|call trace|backtrace|firmware|out of memory|rcu_sched" | tail -n 30
[    0.000000] psci: PSCIv1.1 detected in firmware.
[    0.069170] Call trace:
[    0.692844] qcom_scm firmware:scm: qseecom: found qseecom with version 0x1402000
[    0.692856] qcom_scm firmware:scm: qseecom: untested machine, skipping
[    1.181418] msm_dpu ae01000.display-controller: bound ae94000.dsi (ops 0xffff800081326b38)
[    1.182721] msm_dpu ae01000.display-controller: bound ae90000.displayport-controller (ops 0xffff8000813256b8)
[    1.182952] adreno 3d00000.gpu: supply vdd not found, using dummy regulator
[    1.182984] adreno 3d00000.gpu: supply vddcx not found, using dummy regulator
[    1.192137] msm_dpu ae01000.display-controller: bound 3d00000.gpu (ops 0xffff8000812f2538)
[    1.194733] msm_dpu ae01000.display-controller: [drm:adreno_request_fw] loaded qcom/a650_sqe.fw from new location
[    1.194741] msm_dpu ae01000.display-controller: [drm:adreno_request_fw] loaded qcom/a650_gmu.bin from new location
[    1.195304] [drm] Loaded GMU firmware v2.1.8
[    1.329362] msm_dpu ae01000.display-controller: [drm] fb0: msmdrmfb frame buffer device
[    1.331773] faux_driver regulatory: Direct firmware load for regulatory.db failed with error -2
[    2.666866] kernel-overlays-setup: added firmware from /usr/lib/kernel-overlays/base/lib/firmware
[    4.787036] Call trace:
[  266.904044] adreno 3d00000.gpu: [drm:a6xx_irq] *ERROR* gpu fault ring 0 fence 6b5e status 00E51005 rb 1f90/1feb ib1 000000015C6D9000/0ef1 ib2 00000001AF810C44/0000
[  266.904109] msm_dpu ae01000.display-controller: [drm:recover_worker] *ERROR* 6.5.0.2: hangcheck recover!
[  266.904157] msm_dpu ae01000.display-controller: [drm:recover_worker] *ERROR* 6.5.0.2: offending task: RSX Offloader (/tmp/.mount_2duSTJOv/AppRun.wrapped --no-gui /storage/.config/rpcs3/dev_hdd0/game/NPUA80075/USRDIR/EBOOT.BIN)
[  279.978539] rb 0: fence:    27479/27487
SM8250:~ #`

4. Clean Gold
- BATT: 38% at cleanboot start. 
- BATT: 33% at clean gold, skilled racing 
- HUD after save 78C 7.89 93% NEW:0

5. BATT: 33% at cleanboot start. 
- Freeze at 0'12.300
`SM8250:~ # dmesg | strings | grep -iE "adreno|kgsl|turnip|fence|timeout|msm_dpu|panic|oom|killed process|segfault|sigsegv|hard lockup|watchdog|call trace|backtrace|firmware|out of memory|rcu_sched" | tail -n 30
[    0.000000] psci: PSCIv1.1 detected in firmware.
[    0.068623] Call trace:
[    0.688825] qcom_scm firmware:scm: qseecom: found qseecom with version 0x1402000
[    0.688848] qcom_scm firmware:scm: qseecom: untested machine, skipping
[    1.216681] msm_dpu ae01000.display-controller: bound ae94000.dsi (ops 0xffff800081326b38)
[    1.218140] msm_dpu ae01000.display-controller: bound ae90000.displayport-controller (ops 0xffff8000813256b8)
[    1.218825] adreno 3d00000.gpu: supply vdd not found, using dummy regulator
[    1.218917] adreno 3d00000.gpu: supply vddcx not found, using dummy regulator
[    1.243789] msm_dpu ae01000.display-controller: bound 3d00000.gpu (ops 0xffff8000812f2538)
[    1.247092] msm_dpu ae01000.display-controller: [drm:adreno_request_fw] loaded qcom/a650_sqe.fw from new location
[    1.247110] msm_dpu ae01000.display-controller: [drm:adreno_request_fw] loaded qcom/a650_gmu.bin from new location
[    1.247824] [drm] Loaded GMU firmware v2.1.8
[    1.406002] msm_dpu ae01000.display-controller: [drm] fb0: msmdrmfb frame buffer device
[    1.412612] faux_driver regulatory: Direct firmware load for regulatory.db failed with error -2
[    2.708754] kernel-overlays-setup: added firmware from /usr/lib/kernel-overlays/base/lib/firmware
[  105.421683] adreno 3d00000.gpu: [drm:a6xx_irq] *ERROR* gpu fault ring 0 fence 2393 status 00E59005 rb 19e1/1a65 ib1 0000000149028000/0b13 ib2 000000019A431300/0000
[  105.421728] msm_dpu ae01000.display-controller: [drm:recover_worker] *ERROR* 6.5.0.2: hangcheck recover!
[  105.421748] msm_dpu ae01000.display-controller: [drm:recover_worker] *ERROR* 6.5.0.2: offending task: RSX Offloader (/tmp/.mount_4mJaOgQg/AppRun.wrapped --no-gui /storage/.config/rpcs3/dev_hdd0/game/NPUA80075/USRDIR/EBOOT.BIN)
[  105.969241] rb 0: fence:    9100/9107
SM8250:~ # `

## TESTED CONFIG

`Audio:
  Audio Buffer: 150
  Audio Channel Layout: Automatic
  Audio Device: "@@@default@@@"
  Audio Format: Stereo
  Audio Formats: 0
  Audio Provider: CellAudio
  Channels: 2.0
  Convert to 16 bit: false
  Desired Audio Buffer Duration: 100
  Disable Sampling Skip: false
  Dump to file: false
  Enable Buffering: true
  Enable Time Stretching: true
  Master Volume: 100
  Microphone Devices: "@@@@@@@@@@@@"
  Microphone Type: "Null"
  Music Handler: Qt
  RSXAudio Avport: HDMI 0
  Renderer: Cubeb
  Time Stretching Threshold: 100
Core:
  Accurate Cache Line Stores: false
  Accurate PPU 128-byte Reservation Op Max Length: 0
  Accurate RSX reservation access: false
  Accurate SPU DMA: false
  Accurate SPU Reservations: true
  Allow RSX CPU Preemptions: true
  Assume External Debugger: false
  Clocks scale: 100
  Debug Console Mode: false
  Disable SPU GETLLAR Spin Optimization: false
  Enable Performance Report: false
  HLE lwmutex: false
  Hook static functions: false
  LLVM Precompilation: true
  Libraries Control:
    []
  Lower SPU Priority: false
  MFC Commands Shuffling In Steps: false
  MFC Commands Shuffling Limit: 0
  MFC Commands Timeout: 0
  MFC Debug: false
  Max CPU Preempt Count: 0
  Max LLVM Compile Threads: 0
  Max SPURS Threads: 6
  PPU Accurate Non-Java Mode: false
  PPU Accurate Vector NaN Values: false
  PPU Calling History: false
  PPU Debug: false
  PPU Decoder: Recompiler (LLVM)
  PPU LLVM Greedy Mode: false
  PPU LLVM Java Mode Handling: true
  PPU Profiler: false
  PPU Set FPCC Bits: false
  PPU Set Saturation Bit: false
  PPU Threads: 2
  PPU Vector NaN Handling: true
  Performance Report Threshold: 500
  Precise SPU Verification: false
  Preferred SPU Threads: 3
  RSX FIFO Fetch Accuracy: "Ordered & Atomic"
  SPU Block Size: Mega
  SPU Cache: true
  SPU Debug: false
  SPU Decoder: Recompiler (LLVM)
  SPU GETLLAR Busy Waiting Percentage: 100
  SPU LLVM Lower Bound: 0
  SPU LLVM Upper Bound: 18446744073709551615
  SPU Profiler: false
  SPU Reservation Busy Waiting Enabled: false
  SPU Reservation Busy Waiting Percentage 1: 100
  SPU Verification: true
  SPU Wake-Up Delay: 0
  SPU Wake-Up Delay Thread Mask: 63
  SPU XFloat Accuracy: Approximate
  SPU delay penalty: 3
  SPU loop detection: false
  Save LLVM logs: false
  Set DAZ and FTZ: false
  Sleep Timers Accuracy: As Host
  Stub PPU Traps: 0
  Thread Scheduler Mode: Operating System
  Use Accurate DFMA: true
  Use LLVM CPU: ""
  Usleep Time Addend: 0
Input/Output:
  Allow move hue set by game: false
  Background input enabled: true
  Buzz emulated controller: "Null"
  Camera: "Null"
  Camera ID: Default
  Camera flip: None
  Camera type: Unknown
  Emulated Midi devices: Keyboardßßß@@@Keyboardßßß@@@Keyboardßßß@@@
  Fake Move Rotation Cone: 10
  Fake Move Rotation Cone (Vertical): 10
  GHLtar emulated controller: "Null"
  IO Debug overlay: false
  Keep pads connected: false
  Keyboard: "Null"
  Load SDL GameController Mappings: true
  Lock overlay input to player one: false
  Mouse: Basic
  Mouse Debug overlay: false
  Move: "Null"
  Pad handler mode: Single-threaded
  Pad handler sleep (microseconds): 1000
  Paint move spheres: false
  SDL Camera ID: Default
  Show move cursor: false
  Turntable emulated controller: "Null"
Log:
  {}
Miscellaneous:
  Automatically start games after boot: true
  Enable GameMode: false
  Exit RPCS3 when process finishes: false
  GDB Server: 127.0.0.1:2345
  Pause Emulation During Home Menu: false
  Pause emulation on RPCS3 focus loss: false
  Play music during boot sequence: true
  Prevent display sleep while running games: true
  Show PPU compilation hint: true
  Show RPCN popups: true
  Show analog limiter toggle hint: true
  Show autosave/autoload hint: false
  Show capture hints: true
  Show fatal error hints: false
  Show mouse and keyboard toggle hint: true
  Show pressure intensity toggle hint: true
  Show shader compilation hint: true
  Show trophy popups: true
  Silence All Logs: false
  Start games in fullscreen mode: true
  Use native user interface: true
  Use recursive scan: false
  Window Title Format: "FPS: %F | %R | %V | %T [%t]"
Net:
  Bind address: 0.0.0.0
  Clans Enabled: false
  DNS address: 8.8.8.8
  IP address: 0.0.0.0
  IP swap list: ""
  Internet enabled: Disconnected
  PSN Country: us
  PSN status: Disconnected
  UPNP Enabled: false
Savestate:
  Compatible Savestate Mode: false
  Inspection Mode Savestates: false
  Maximum SaveState Files: 4
  Maximum SaveState Files Space (MiB): 4096
  Save Disc Game Data: false
  Start Paused: false
  Suspend Emulation Savestate Mode: false
System:
  Console PSID: 0x730A344806DDD47D10D3C0A44601EAFC
  Console time offset (s): 0
  Date Format: ddmmyyyy
  Enter button assignment: Enter with cross
  HDD Model Name: ""
  HDD Serial Number: ""
  Keyboard Type: English keyboard (US standard)
  Language: English (US)
  License Area: SCEA
  Process ARGV:
    {}
  System Name: RPCS3-958
  Time Format: clock24
VFS:
  Disk cache maximum size (MB): 5120
  Empty /dev_hdd0/tmp/: true
  Enable /host_root/: false
  Initialize Directories: true
  Limit disk cache size: false
Video:
  3D Display Enabled: false
  3D Display Mode: Disabled
  Accurate ZCULL stats: true
  Allow Host GPU Labels: false
  Anisotropic Filter Override: 0
  Aspect ratio: 16:9
  Consecutive Frames To Draw: 1
  Consecutive Frames To Skip: 1
  DECR memory layout: false
  Debug Program Analyser: false
  Debug output: false
  Debug overlay: false
  Disable Asynchronous Memory Manager: false
  Disable FIFO Reordering: false
  Disable Hardware ColorSpace Remapping: false
  Disable MSL Fast Math: false
  Disable On-Disk Shader Cache: false
  Disable Vertex Cache: false
  Disable Video Output: false
  Disable Vulkan Memory Allocator: false
  Disable ZCull Occlusion Queries: true
  Driver Recovery Timeout: 1000000
  Driver Wake-Up Delay: 0
  Enable Frame Skip: false
  FidelityFX CAS Sharpening Intensity: 50
  Force CPU Blit: false
  Force Hardware MSAA Resolve: false
  Force High Precision Z buffer: false
  Frame limit: 30
  Framebuffer Aliasing Heuristic Bias: Auto
  Handle RSX Memory Tiling: false
  Log shader programs: false
  MSAA: Auto
  Minimum Scalable Dimension: 16
  Multithreaded RSX: true
  Output Scaling Mode: Bilinear
  Performance Overlay:
    Body Background (hex): "#002339FF"
    Body Color (hex): "#FFE138FF"
    Center Horizontally: false
    Center Vertically: false
    Detail level: Medium
    Enable Framerate Graph: false
    Enable Frametime Graph: false
    Enabled: false
    Font: n023055ms.ttf
    Font size (px): 10
    Framerate datapoints: 50
    Framerate graph detail level: All
    Frametime datapoints: 170
    Frametime graph detail level: All
    Horizontal Margin (%): 4
    Metrics update interval (ms): 350
    Opacity (%): 70
    Position: Top Left
    Title Background (hex): "#00000000"
    Title Color (hex): "#F26C24FF"
    Use Window Space: false
    Vertical Margin (%): 7
  RSX FIFO Accuracy: Ordered
  Read Color Buffers: false
  Read Depth Buffer: false
  Record With Overlays: true
  Relaxed ZCULL Sync: true
  Renderdoc Compatibility Mode: false
  Renderer: Vulkan
  Resolution: 1280x720
  Resolution Scale: 100
  Resolution Scale Threshold: 512
  Screen size in inches: 24
  Second Frame Limit: 0
  Shader Compiler Threads: 0
  Shader Loading Dialog:
    Allow custom background: true
    Blur effect strength: 0
    Darkening effect strength: 30
  Shader Mode: Async Recompiler with Shader Interpreter
  Shader Precision: Low
  Stretch To Display Area: false
  Strict Rendering Mode: false
  Strict Texture Flushing: false
  Texture LOD Bias Addend: 0
  Use GPU texture scaling: false
  Use full RGB output range: true
  VSync: false
  VSync Mode: Disabled
  Vblank Frequency: 60Hz
  Vblank NTSC Fixup: false
  Vblank Rate: 60
  Vulkan:
    Adapter: Turnip Adreno (TM) 650
    Asynchronous Queue Scheduler: Safe
    Asynchronous Texture Streaming: true
    Exclusive Fullscreen Mode: Automatic
    Force primitive restart flag: false
    Use Re-BAR for GPU uploads: true
    VRAM allocation limit (MB): 65536
  Write Color Buffers: false
  Write Depth Buffer: false
  ZCULL Accuracy: Relaxed`

