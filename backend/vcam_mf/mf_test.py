"""Test the Apex Cam Media Foundation bridge end to end.

Launches ApexCamVCam.exe (which publishes the "Apex Cam" MF camera) and feeds it
a TEST CARD via shared memory — coloured halves + TOP/BOTTOM/L/R labels + a moving
dot — so we can confirm in WhatsApp that: (a) the camera shows our pixels, and
(b) orientation and colour are right. Toggle --flip / --rgba to correct those.

    python mf_test.py            # BGRA, no flip
    python mf_test.py --flip     # vertical flip (bottom-up RGB32)
    python mf_test.py --rgba     # swap to RGBA byte order
"""
import mmap
import os
import subprocess
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "ApexCamVCam.exe")
W, H = 1280, 720
FRAME = W * H * 4
FLIP = "--flip" in sys.argv
RGBA = "--rgba" in sys.argv

proc = subprocess.Popen([EXE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, bufsize=1)
started = False
for _ in range(60):
    line = proc.stdout.readline()
    if not line:
        break
    print("BRIDGE:", line.strip(), flush=True)
    if "APEXCAM_STARTED" in line:
        started = True
        break
    if "FAILED" in line or "ERROR" in line:
        break
if not started:
    print("bridge did not start", flush=True)
    proc.terminate()
    sys.exit(1)

mm = mmap.mmap(-1, FRAME, tagname="Local\\ApexCamFrame", access=mmap.ACCESS_WRITE)
print(f"feeding test card (flip={FLIP} rgba={RGBA}); kill this process to stop", flush=True)
t = 0
try:
    while True:
        img = np.zeros((H, W, 3), np.uint8)   # BGR
        img[:, :W // 2] = (255, 0, 0)          # left half BLUE (in BGR)
        img[:, W // 2:] = (0, 0, 255)          # right half RED
        cv2.putText(img, "TOP", (W // 2 - 90, 110), cv2.FONT_HERSHEY_SIMPLEX, 3.0, (255, 255, 255), 8)
        cv2.putText(img, "BOTTOM", (W // 2 - 200, H - 40), cv2.FONT_HERSHEY_SIMPLEX, 3.0, (0, 255, 0), 8)
        cv2.putText(img, "L", (20, H // 2 + 20), cv2.FONT_HERSHEY_SIMPLEX, 4.0, (255, 255, 255), 12)
        cv2.putText(img, "R", (W - 90, H // 2 + 20), cv2.FONT_HERSHEY_SIMPLEX, 4.0, (255, 255, 255), 12)
        x = int((t * 12) % (W - 60)) + 30
        cv2.circle(img, (x, H // 2), 25, (0, 255, 255), -1)
        frame = cv2.cvtColor(img, cv2.COLOR_BGR2RGBA if RGBA else cv2.COLOR_BGR2BGRA)
        if FLIP:
            frame = np.flipud(frame)
        mm.seek(0)
        mm.write(np.ascontiguousarray(frame).tobytes())
        t += 1
        time.sleep(1 / 30)
except KeyboardInterrupt:
    pass
finally:
    try:
        proc.stdin.write("stop\n")
        proc.stdin.flush()
    except Exception:
        pass
    proc.terminate()
