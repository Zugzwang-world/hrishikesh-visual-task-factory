#!/usr/bin/env bash
# Assemble the demo video with ffmpeg (brief VTF-09, section 3.4).
#
#   scripts/make_video.sh                 # the founder's clips from video/inbox/ (gitignored)
#   scripts/make_video.sh --placeholders  # labelled slates for every desk shot: proves the pipeline
#
# The edit list is docs/video/edit.yaml. scripts/video_kit.py plan matches the founder's desk
# clips and voice-over to the shots, draws the title cards and slates, stretches a shot whose
# voice-over runs long, and times the captions (out/video/plan.tsv, captions.ass/.srt). This
# script then normalises every piece to 1920 x 1080, 30 fps, H.264 and 48 kHz stereo AAC, joins
# them, burns in the captions and evens out the loudness:
#
#   out/video/vtf_demo.mp4   the video          out/video/captions.srt   the same captions, separate
#
# If burning the captions fails, the video ships without them and captions.srt goes beside it
# (the brief's fallback). Screen segments come from docs/video/segments/ (scripts/record_screens/).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$ROOT/out/video"
cd "$ROOT"

uv run scripts/video_kit.py plan "$@"

PIECES="$WORK/pieces"
mkdir -p "$PIECES"
: > "$WORK/concat.txt"
ENC=(-c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -r 30 -c:a aac -b:a 192k -ar 48000 -ac 2)
SCALE="scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1"

while IFS=$'\t' read -r idx sid kind video is_image zoom audio own_audio dur; do
  out="$PIECES/$idx.mp4"
  frames=$(python3 -c "print(round($dur * 30))")
  if [[ "$is_image" == "1" ]]; then
    if [[ "$zoom" == "1" ]]; then  # a still: a slow push-in over the whole shot
      vf="scale=3840:2160:force_original_aspect_ratio=decrease,pad=3840:2160:(ow-iw)/2:(oh-ih)/2:color=white,zoompan=z='1+0.06*on/$frames':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=$frames:s=1920x1080:fps=30,setsar=1,format=yuv420p"
    else
      vf="$SCALE,fps=30,format=yuv420p"
    fi
    vin=(-loop 1 -framerate 30 -t "$dur" -i "$video")
  else  # a clip: hold its last frame if the shot runs longer (a long voice-over)
    vf="$SCALE,fps=30,format=yuv420p,tpad=stop_mode=clone:stop_duration=$dur"
    vin=(-i "$video")
  fi
  if [[ "$own_audio" == "1" ]]; then
    ain=() ; amap="0:a:0"
  elif [[ "$audio" != "-" ]]; then
    ain=(-i "$audio") ; amap="1:a:0"
  else
    ain=(-f lavfi -t "$dur" -i "anullsrc=r=48000:cl=stereo") ; amap="1:a:0"
  fi
  ffmpeg -nostdin -v error -y "${vin[@]}" "${ain[@]}" \
    -filter_complex "[0:v]$vf[v];[$amap]aresample=48000,apad[a]" \
    -map "[v]" -map "[a]" -t "$dur" "${ENC[@]}" "$out"
  echo "file '$out'" >> "$WORK/concat.txt"
  printf '  %s %-3s %-6s %6.2f s  %s\n' "$idx" "$sid" "$kind" "$dur" "$(basename "$video")"
done < "$WORK/plan.tsv"

ffmpeg -v error -y -f concat -safe 0 -i "$WORK/concat.txt" -c copy "$WORK/joined.mp4"

FINAL="$WORK/vtf_demo.mp4"
if ffmpeg -v error -y -i "$WORK/joined.mp4" \
     -vf "ass=$WORK/captions.ass" -af "loudnorm=I=-16:TP=-1.5:LRA=11" \
     -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -c:a aac -b:a 192k -ar 48000 \
     -movflags +faststart "$FINAL"; then
  echo "make_video: $FINAL with burnt-in captions ($(du -h "$FINAL" | cut -f1)); also $WORK/captions.srt"
else
  echo "make_video: burning captions failed; shipping without them, captions in $WORK/captions.srt" >&2
  ffmpeg -v error -y -i "$WORK/joined.mp4" -af "loudnorm=I=-16:TP=-1.5:LRA=11" -c:v copy \
    -c:a aac -b:a 192k -movflags +faststart "$FINAL"
fi
ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$FINAL" | \
  python3 -c "import sys; t=float(sys.stdin.read()); print(f'make_video: length {int(t//60)}:{t%60:04.1f}')"
