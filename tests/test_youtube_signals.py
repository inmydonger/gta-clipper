"""Pure helpers of youtube_signals (gta-clipper's replay/reaction/cutscene signals)."""
import twitch_signals
import youtube_signals as ys


def _heat(values, seg=10.0):
    return [{"start_time": i * seg, "end_time": (i + 1) * seg, "value": v}
            for i, v in enumerate(values)]


def test_heatmap_peaks_merges_runs_and_skips_the_opening():
    # 100 segments of 10 s; the first always peaks, then a run at 40-60 s.
    values = [1.0] + [0.1] * 3 + [0.6, 0.9] + [0.2] * 94
    peaks = ys.heatmap_peaks(_heat(values), duration=1000)
    assert peaks == [{"start": 40.0, "end": 60.0, "views": 90}]


def test_heatmap_peaks_empty_or_garbage():
    assert ys.heatmap_peaks(None) == []
    assert ys.heatmap_peaks([{"start_time": "x"}]) == []


def test_shift_spans_drops_what_ends_before_the_cut():
    spans = [{"start": 10, "end": 20, "views": 50}, {"start": 100, "end": 130, "views": 80}]
    assert ys.shift_spans(spans, 90) == [{"start": 10, "end": 40, "views": 80}]


def test_reaction_times_finds_phrases_across_word_tokens():
    words = [{"w": w, "s": float(i)} for i, w in enumerate(
        "alright chat Wait, wait! what is that behind the billboard? no way".split())]
    # "wait wait" at 2, "what is that" at 4, "behind the" at 7, "no way" at 10
    assert ys.reaction_times(words) == [2.0, 4.0, 7.0, 10.0]


def test_parse_video_meta_flags_letterbox_not_black_or_full_frames():
    text = "\n".join([
        "frame:0 pts:0 pts_time:0",                     # no cropdetect yet
        "frame:1 pts:1 pts_time:5",                     # full frame
        "lavfi.cropdetect.y1=0", "lavfi.cropdetect.y2=179",
        "frame:2 pts:2 pts_time:10",                    # 2.39:1 bars
        "lavfi.cropdetect.y1=22", "lavfi.cropdetect.y2=157",
        "frame:3 pts:3 pts_time:15",                    # near-black frame
        "lavfi.cropdetect.y1=80", "lavfi.cropdetect.y2=100",
    ])
    out = ys.parse_video_meta(text)
    assert out == {"keyframes": [0.0, 5.0, 10.0, 15.0], "letterbox": [10.0]}


def test_loud_seconds_is_the_loudest_tenth_ignoring_silence():
    lines = []
    for s in range(20):
        v = -10.0 if s in (7, 13) else -30.0
        lines += [f"frame:{s} pts:{s} pts_time:{s}", f"lavfi.r128.M={v}"]
    lines += ["frame:99 pts:99 pts_time:99", "lavfi.r128.M=-120"]
    assert ys.loud_seconds("\n".join(lines)) == [7, 13]


def test_window_signals_summarises_each_window():
    windows = [{"id": "w1", "start": 0, "end": 60}, {"id": "w2", "start": 60, "end": 120}]
    words = [{"w": "no", "s": 70.0}, {"w": "way", "s": 70.4}]
    media = {"keyframes": [0, 20, 40, 60, 80, 100], "letterbox": [60, 80], "loud": [75, 76]}
    replay = twitch_signals.window_heat(windows, [{"start": 65, "end": 75, "views": 90}])
    sig = ys.window_signals(windows, words, media, replay)
    assert sig["w1"] == {"cuts_per_min": 3.0}
    assert sig["w2"] == {"reactions": 1, "reaction_times": [70.0], "cutscene_pct": 67,
                         "cuts_per_min": 3.0, "loud_seconds": 2,
                         "replay_heat": 0.9, "replay_moments": [[65, 75]]}


def test_is_youtube():
    assert ys._is_youtube("https://www.youtube.com/watch?v=x")
    assert ys._is_youtube("https://youtu.be/x")
    assert not ys._is_youtube("https://www.twitch.tv/videos/1")
    assert not ys._is_youtube("https://evil-youtube.com/watch")


def test_heatmap_peaks_keeps_only_spikes_when_the_curve_sits_high():
    # A trailer: everything above 0.5, two real spikes.
    values = [1.0] + [0.6] * 40 + [0.95] + [0.6] * 40 + [0.9] + [0.6] * 17
    peaks = ys.heatmap_peaks(_heat(values), duration=1000)
    assert [(p["start"], p["views"]) for p in peaks] == [(410.0, 95), (820.0, 90)]


# --- composite score and cutscene flag --------------------------------------

def test_composite_reweights_when_a_signal_is_missing():
    sig = {"reactions": 3}
    # no heatmap, no media: only ai (0.25) and reaction (0.25) count, equally
    score, parts, flags = ys.composite_score(60, sig, 40, have_replay=False, have_media=False)
    assert parts == {"ai": 60, "reaction": 100} and score == 80 and flags == []


def test_composite_full_weights_and_cutscene_sweet_spot():
    sig = {"reactions": 0, "replay_heat": 0.9, "cutscene_pct": 40, "loud_seconds": 8}
    score, parts, flags = ys.composite_score(80, sig, 40, have_replay=True, have_media=True)
    assert parts == {"ai": 80, "reaction": 0, "replay": 90, "cutscene": 100, "action": 100}
    # .25*.8 + .35*.9 + .10*1 + .05*1 = .665 over a total weight of 1.0
    assert score == 66
    assert flags == []


def test_mostly_cutscene_is_flagged_and_scores_lower_on_that_part():
    _, parts, flags = ys.composite_score(80, {"cutscene_pct": 90}, 40, True, True)
    assert parts["cutscene"] == 30 and flags == ["mostly_cutscene"]


def test_clip_replay_heat_uses_overlap_not_midpoint():
    peaks = [{"start": 50, "end": 90, "views": 80}, {"start": 200, "end": 210, "views": 95}]
    assert ys.clip_replay_heat(peaks, 45, 60) == 0.8      # peak's midpoint (70) is outside
    assert ys.clip_replay_heat(peaks, 100, 150) == 0.0
    assert ys.clip_replay_heat(None, 0, 10) == 0.0


def test_score_clips_keeps_the_ai_score_and_writes_the_composite():
    shorts = [{"start": 60, "end": 100, "predicted_score": 70}]
    words = [{"w": "no", "s": 65.0}, {"w": "way", "s": 65.3}]
    media = {"keyframes": [60, 70, 80, 90], "letterbox": [60, 70, 80, 90], "loud": []}
    ys.score_clips(shorts, words, media, [{"start": 55, "end": 70, "views": 60}])
    s = shorts[0]
    assert s["ai_score"] == 70
    assert s["signals"]["cutscene_pct"] == 100 and s["signals"]["replay_heat"] == 0.6
    assert s["flags"] == ["mostly_cutscene"]
    assert set(s["score_breakdown"]) == {"ai", "reaction", "replay", "cutscene", "action"}
    assert 0 <= s["predicted_score"] <= 100


def test_no_cutscene_is_neutral_not_a_penalty():
    with_none = ys.composite_score(80, {"loud_seconds": 8}, 40, False, True)
    assert "cutscene" not in with_none[1]
    # ai .25*.8 + action .05*1 over .55 (reaction 0 at .25)
    assert with_none[0] == round(100 * (0.25 * 0.8 + 0.05) / 0.55)
