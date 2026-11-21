"""Signals beyond the words, for the GTA VI moment picker.

Easter eggs and mysteries are quiet moments a transcript ranks low, so each
scoring window also carries:

- ``replay_heat`` / ``replay_moments``: YouTube's "Most replayed" graph
  (yt-dlp ``heatmap``), where viewers rewound to look again.
- ``reactions`` / ``reaction_times``: the commentator's discovery phrases.
- ``cutscene_pct``: share of the window's keyframes with letterbox bars.
- ``loud_seconds``: seconds in the video's own loudest tenth.
- ``cuts_per_min``: keyframes per minute; x264 puts one on most scene cuts.

The model weighs them through the gta-clipper skill; replay peaks also
reserve shortlist slots (``twitch_signals.promote_hot_windows``). Everything
is best effort: a failure returns empty and the job runs as it did before.
``CLIP_SIGNALS=0`` turns the whole module off.
"""
import os
import re
import subprocess
import tempfile
from typing import Dict, List, Optional
from urllib.parse import urlparse

ENABLED = os.environ.get("CLIP_SIGNALS", "1").strip() != "0"

HEAT_MIN = 0.5           # heatmap value (0..1, max-normalised) that counts as a peak...
HEAT_PROMINENCE = 0.5    # ...and at least halfway from the video's median to its top:
                         # a short trailer's curve sits high all the way through
HEAT_SKIP_START = 0.03   # the opening always peaks: everyone watches the start
FRAME_W, FRAME_H = 320, 180
BAR_MIN = 0.10           # each letterbox bar covers at least 10% of the height
LOUD_PERCENTILE = 0.90

# Composite clip score (0-100). A component whose signal the video cannot have
# (no heatmap on a new upload, no media pass) is left out and the rest are
# reweighted, so a missing graph never drags a clip down.
# ponytail: hand-set weights; re-fit them from posted-clip views (calibration log).
SCORE_WEIGHTS = {"replay": 0.35, "reaction": 0.25, "ai": 0.25, "cutscene": 0.10, "action": 0.05}
REACTIONS_FOR_FULL = 3       # reaction phrases in a clip that max that component
CUTSCENE_SWEET = (20, 70)    # cutscene with the commentator around it
CUTSCENE_FLAG_PCT = 70       # above: mostly Rockstar's footage, flag the clip

# ponytail: phrase list, not a classifier. Add phrases the clipped streamers use.
REACTION_PHRASES = [
    "wait wait", "no way", "did you see", "what is that", "what was that",
    "what's that", "look at that", "look at this", "oh my god", "what the",
    "are you kidding", "hold on", "hold up", "how did", "losing it",
    "easter egg", "secret", "hidden", "behind the", "nobody noticed",
]
_TOKEN = re.compile(r"[a-z0-9']+")


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def heatmap_peaks(heatmap, duration=None) -> List[dict]:
    """Contiguous heatmap stretches at or above ``HEAT_MIN``, skipping the
    opening, as ``{"start", "end", "views"}`` (views = peak value x 100) so
    ``twitch_signals.window_heat`` and ``promote_hot_windows`` take them."""
    segs = []
    for h in heatmap or []:
        try:
            segs.append((float(h["start_time"]), float(h["end_time"]), float(h["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not segs:
        return []
    segs.sort()
    total = float(duration or 0) or segs[-1][1]
    skip = total * HEAT_SKIP_START
    body = sorted(v for s, _, v in segs if s >= skip)
    if not body:
        return []
    median = body[len(body) // 2]
    floor = max(HEAT_MIN, median + (body[-1] - median) * HEAT_PROMINENCE)
    spans = []
    for s, e, v in segs:
        if v < floor or s < skip:
            continue
        if spans and s <= spans[-1]["end"] + 0.01:
            spans[-1]["end"] = e
            spans[-1]["peak"] = max(spans[-1]["peak"], v)
        else:
            spans.append({"start": s, "end": e, "peak": v})
    return [{"start": round(p["start"], 3), "end": round(p["end"], 3),
             "views": int(round(p["peak"] * 100))} for p in spans]


def shift_spans(spans: List[dict], start_seconds: float) -> List[dict]:
    """Source-video time -> time in a file that starts ``start_seconds`` in."""
    out = []
    for s in spans:
        a, b = s["start"] - start_seconds, s["end"] - start_seconds
        if b > 0:
            out.append({**s, "start": round(max(0.0, a), 3), "end": round(b, 3)})
    return out


def reaction_times(words: List[dict]) -> List[float]:
    """Start time of every reaction phrase in ``[{'w', 's', 'e'}]`` words."""
    toks = []
    for w in words or []:
        for t in _TOKEN.findall(str(w.get("w", "")).lower()):
            toks.append((t, float(w.get("s") or 0)))
    phrases = [p.split() for p in REACTION_PHRASES]
    times = []
    i = 0
    while i < len(toks):
        hit = next((p for p in phrases
                    if [t for t, _ in toks[i:i + len(p)]] == p), None)
        if hit:
            times.append(toks[i][1])
            i += len(hit)
        else:
            i += 1
    return times


def parse_video_meta(text: str) -> Dict[str, List[float]]:
    """ffmpeg ``metadata=print`` of the keyframe pass -> keyframe times and
    the letterboxed ones. A frame is letterboxed when bars top and bottom
    each cover ``BAR_MIN`` of the height and the picture between is real
    (a black frame crops to almost nothing)."""
    frames = []
    cur = None
    for line in text.splitlines():
        if line.startswith("frame:"):
            m = re.search(r"pts_time:([0-9.]+)", line)
            cur = {"t": float(m.group(1)) if m else 0.0}
            frames.append(cur)
        elif cur is not None and "=" in line:
            k, v = line.split("=", 1)
            cur[k.strip()] = v.strip()
    keyframes, letterbox = [], []
    for f in frames:
        keyframes.append(f["t"])
        try:
            y1 = float(f["lavfi.cropdetect.y1"])
            y2 = float(f["lavfi.cropdetect.y2"])
        except (KeyError, ValueError):
            continue
        top, bottom = y1, (FRAME_H - 1) - y2
        if (top >= FRAME_H * BAR_MIN and bottom >= FRAME_H * BAR_MIN
                and (y2 - y1) >= FRAME_H * 0.4):
            letterbox.append(f["t"])
    return {"keyframes": keyframes, "letterbox": letterbox}


def loud_seconds(text: str) -> List[int]:
    """ffmpeg ``ametadata=print`` of ebur128 momentary loudness -> the seconds
    in the video's own loudest tenth (silence below -70 LUFS left out)."""
    per_sec: Dict[int, float] = {}
    t = None
    for line in text.splitlines():
        if line.startswith("frame:"):
            m = re.search(r"pts_time:([0-9.]+)", line)
            t = int(float(m.group(1))) if m else None
        elif line.startswith("lavfi.r128.M=") and t is not None:
            try:
                v = float(line.split("=", 1)[1])
            except ValueError:
                continue
            per_sec[t] = max(per_sec.get(t, -200.0), v)
    voiced = sorted(v for v in per_sec.values() if v > -70)
    if len(voiced) < 10:
        return []
    cut = voiced[int(len(voiced) * LOUD_PERCENTILE)]
    return sorted(s for s, v in per_sec.items() if v >= cut)


def window_signals(windows: List[dict], words: Optional[List[dict]] = None,
                   media: Optional[dict] = None,
                   replay: Optional[Dict[str, dict]] = None) -> Dict[str, dict]:
    """``{window_id: {signal: value}}``, only the signals that say something.
    ``replay`` is ``twitch_signals.window_heat(windows, heatmap_peaks(...))``."""
    reacts = reaction_times(words) if words else []
    media = media or {}
    kfs, boxed, loud = media.get("keyframes") or [], set(media.get("letterbox") or []), media.get("loud") or []
    out = {}
    for w in windows:
        a, b = float(w["start"]), float(w["end"])
        mins = max(b - a, 1.0) / 60
        sig = {}
        r = [t for t in reacts if a <= t < b]
        if r:
            sig["reactions"] = len(r)
            sig["reaction_times"] = [round(t, 1) for t in r[:3]]
        k = [t for t in kfs if a <= t < b]
        if k:
            pct = round(100 * sum(1 for t in k if t in boxed) / len(k))
            if pct:
                sig["cutscene_pct"] = pct
            sig["cuts_per_min"] = round(len(k) / mins, 1)
        n_loud = sum(1 for s in loud if a <= s < b)
        if n_loud:
            sig["loud_seconds"] = n_loud
        h = (replay or {}).get(w["id"])
        if h:
            sig["replay_heat"] = round(max(1, h["views"]) / 100 / max(1, h["clips"]), 2)
            sig["replay_moments"] = h["spans"][:3]
        if sig:
            out[w["id"]] = sig
    return out


def clip_replay_heat(peaks: Optional[List[dict]], start: float, end: float) -> float:
    """Highest replay peak (0..1) overlapping ``[start, end]``. Overlap, not
    midpoint: a clip that opens a few seconds before a long peak still has it."""
    return max((p["views"] / 100 for p in peaks or []
                if p["start"] < end and p["end"] > start), default=0.0)


def composite_score(ai_score, sig: dict, duration: float,
                    have_replay: bool, have_media: bool):
    """``(score 0-100, breakdown {component: 0-100}, flags [str])``."""
    try:
        ai = min(1.0, max(0.0, float(ai_score) / 100))
    except (TypeError, ValueError):
        ai = 0.0
    parts = {"ai": ai, "reaction": min(1.0, sig.get("reactions", 0) / REACTIONS_FOR_FULL)}
    if have_replay:
        parts["replay"] = sig.get("replay_heat", 0.0)
    pct = sig.get("cutscene_pct", 0)
    if have_media:
        lo, hi = CUTSCENE_SWEET
        if pct >= lo:  # no cutscene is neutral: most easter eggs happen in free roam
            parts["cutscene"] = 1.0 if pct <= hi else 0.3
        # Loud seconds are the video's own top tenth: a clip with twice its
        # share (20% of its length) maxes the action component.
        parts["action"] = min(1.0, sig.get("loud_seconds", 0) / max(1.0, 0.2 * duration))
    weight = sum(SCORE_WEIGHTS[k] for k in parts)
    score = round(100 * sum(SCORE_WEIGHTS[k] * v for k, v in parts.items()) / weight)
    breakdown = {k: round(v * 100) for k, v in parts.items()}
    flags = ["mostly_cutscene"] if pct > CUTSCENE_FLAG_PCT else []
    return score, breakdown, flags


def score_clips(shorts: List[dict], words=None, media=None, replay_peaks=None) -> None:
    """Measure each clip's own signals and score it, in place.

    ``ai_score`` keeps the model's number; ``predicted_score`` becomes the
    composite, so everything downstream that ranks, sorts or shows a clip's
    score (render order, the dashboard badge) uses it unchanged.
    """
    have_replay, have_media = bool(replay_peaks), bool(media)
    for s in shorts:
        a, b = float(s.get("start") or 0), float(s.get("end") or 0)
        sig = window_signals([{"id": "c", "start": a, "end": b}], words, media).get("c", {})
        heat = clip_replay_heat(replay_peaks, a, b)
        if heat:
            sig["replay_heat"] = round(heat, 2)
        s["signals"] = sig
        s["ai_score"] = s.get("predicted_score")
        s["predicted_score"], s["score_breakdown"], s["flags"] = composite_score(
            s["ai_score"], sig, b - a, have_replay, have_media)


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def _is_youtube(url) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


def replay_peaks_for_job(url) -> List[dict]:
    """Most-replayed peaks of a YouTube URL, in the time of the file clipped
    (``SOURCE_START_SECONDS`` when ``MAX_SOURCE_MINUTES`` cuts a stretch)."""
    if not ENABLED or not _is_youtube(url):
        return []
    try:
        import yt_dlp
        import ssrf_guard
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
        with ssrf_guard.guarded(), yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # bot check, network, private video: carry on without
        print(f"   ℹ️ Replay heatmap unavailable ({type(e).__name__}).")
        return []
    peaks = heatmap_peaks((info or {}).get("heatmap"), (info or {}).get("duration"))
    if os.environ.get("MAX_SOURCE_MINUTES", "").strip():
        try:
            peaks = shift_spans(peaks, float(os.environ.get("SOURCE_START_SECONDS", "0") or 0))
        except ValueError:
            pass
    print(f"   🔁 Replay heatmap: {len(peaks)} peak(s)" if peaks
          else "   🔁 Replay heatmap: none (new or low-view video).")
    return peaks


def media_signals(video_path) -> dict:
    """One ffmpeg pass: letterbox on keyframes only (fast: ~1 s per 2 min of
    1080p on an M1) plus momentary loudness over the whole audio track."""
    if not ENABLED or not video_path or not os.path.isfile(video_path):
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        vfile, afile = os.path.join(tmp, "v.txt"), os.path.join(tmp, "a.txt")
        graph = (f"[0:v]scale={FRAME_W}:{FRAME_H},cropdetect=limit=0.1:round=2:reset=1,"
                 f"metadata=print:file={vfile}[v];"
                 f"[0:a]ebur128=metadata=1,ametadata=print:key=lavfi.r128.M:file={afile}[a]")
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-skip_frame", "nokey", "-i", video_path,
               "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-f", "null", "-"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=1800, check=True)
        except (subprocess.SubprocessError, OSError) as e:
            print(f"   ℹ️ Media signals skipped ({type(e).__name__}).")
            return {}
        out = {}
        if os.path.isfile(vfile):
            with open(vfile) as f:
                out.update(parse_video_meta(f.read()))
        if os.path.isfile(afile):
            with open(afile) as f:
                out["loud"] = loud_seconds(f.read())
    print(f"   🎞️ Media signals: {len(out.get('keyframes', []))} keyframes, "
          f"{len(out.get('letterbox', []))} letterboxed, {len(out.get('loud', []))} loud s.")
    return out
