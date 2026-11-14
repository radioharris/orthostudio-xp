#!/bin/bash
# series.sh LABEL COUNT [SCENARIO]: COUNT flights of X-Plane alone (fly.py --streamer none), each
# ended at 240 s of path; a minute without a frame is a crash, which ends the series. Logs in
# build/flights/xplane-debug. XPLANE: X-Plane's folder (~/X-Plane 12 by default).
here=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$here/../../../.." && pwd)
XP=${XPLANE:-$HOME/X-Plane 12}; F="$XP/Output/osxp_test_pilot/frames.csv"
out="$repo/build/flights/xplane-debug"; mkdir -p "$out"
label=$1; count=${2:-3}; scenario=${3:-crash-croisiere}
cd "$repo" || exit 1
echo "plugins: $(ls "$XP/Resources/plugins" | grep -v -E '^XP(LM|Widgets)' | tr '\n' ' ')"
for n in $(seq 1 "$count"); do
  while pgrep -x X-Plane >/dev/null; do sleep 2; done
  stop="$out/stop-$label$n.log"; python3 "$here/stop_at.py" 240 > "$stop" 2>&1 &
  .venv/bin/python -u tools/live/flights/fly.py --streamer none "$scenario" > "$out/fly-$label$n.log" 2>&1 &
  started=$(date +%s)
  while true; do
    sleep 2; now=$(date +%s)
    if [ -f "$F" ] && [ "$(stat -f %m "$F")" -ge "$started" ]; then
      age=$(( now - $(stat -f %m "$F") )); t=$(tail -1 "$F" | cut -d, -f2)
      if [ $age -ge 60 ]; then echo "$(date +%H:%M:%S) $label run $n: CRASH, no frame for ${age} s at path ${t} s"; exit 0; fi
    fi
    # stop_at.py says when the path passed 240 s; fly.py moves frames.csv away right after
    if grep -qs "stop asked" "$stop"; then echo "$(date +%H:%M:%S) $label run $n: past 4 min, no crash"; break; fi
    if [ $(( now - started )) -ge 900 ]; then echo "$label run $n: 15 min without a result"; exit 1; fi
  done
  sleep 15
done
echo "$label: $count runs, none crashed"
