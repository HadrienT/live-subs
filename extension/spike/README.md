# WP02 — capture spike

Throw-away extension that answers questions Q1–Q8 of
[`blueprint/wp/02-capture-spike.md`](../../blueprint/wp/02-capture-spike.md) on a
real YouTube live. Every observation is timestamped and saved in a `.log` file
downloaded next to the 60 s WAV.

1. On the server: `cd extension/spike && uv run --project ../../server python echo_server.py`
2. On the PC: `npx web-ext run --source-dir extension/spike --start-url <live URL>`
   (or `about:debugging` → *Load Temporary Add-on* → `manifest.json`).
3. A panel appears bottom-right. Run the combinations below, one per page load:

| Run | capture | route | worklet | What to do during the 60 s |
|---|---|---|---|---|
| A | mozCaptureStream | ✔ | auto | Listen (Q2), mute YouTube for ~10 s then unmute, move its volume slider (Q3) |
| B | mozCaptureStream | ✘ | auto | Is the stream still audible without re-routing? (Q2) |
| C | createMediaElementSource | ✔ | auto | Same as A |
| D | mozCaptureStream | ✔ | ext / blob / script | One run each: which worklet loading works under YouTube's CSP (Q4) |
| E | best of the above | ✔ | auto, *also WS from content* | Q6 both ways |
| F | best of the above | ✔ | auto | Let an ad play, change quality, seek back in the DVR, click another video (Q7) |

4. Check the WAV (Q5): `sox spike-*.wav -n spectrogram` or Audacity — nothing above
   8 kHz, no aliasing whistle, voice intelligible.
5. Record the answers, dated, with the Firefox version, under ADR-001 / ADR-004 in
   `blueprint/decisions.md`, and move the WAVs to `benchmarks/data/` (git-ignored).
