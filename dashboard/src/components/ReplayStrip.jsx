// gta-clipper: the source video's "Most replayed" curve with every clip marked
// on it, so you can see which clips sit on what viewers rewound to watch again.
// Data: clip.source_replay = {duration, points: [[t, 0..1], ...]} (youtube_signals.score_clips).

const W = 1000;
const H = 56;

const fmt = (s) => {
  const t = Math.max(0, Math.round(s));
  const m = Math.floor(t / 60);
  return `${m}:${String(t % 60).padStart(2, '0')}`;
};

const tone = (score) => (score >= 80 ? 'border-ok bg-ok/20 text-ok'
  : score >= 50 ? 'border-brass bg-brass/20 text-brass'
    : 'border-ink2/60 bg-ink2/10 text-ink2');

export default function ReplayStrip({ clips }) {
  const src = (clips || []).find((c) => c?.source_replay?.points?.length)?.source_replay;
  if (!src || !(src.duration > 0)) return null;
  const D = src.duration;
  const x = (t) => (Math.min(Math.max(t, 0), D) / D) * W;
  const pts = src.points;
  const area = `M0,${H} ${pts.map(([t, v]) => `L${x(t).toFixed(1)},${(H - v * (H - 4)).toFixed(1)}`).join(' ')} L${W},${H} Z`;
  const pct = (t) => `${(Math.min(Math.max(t, 0), D) / D) * 100}%`;
  const onPeak = clips.filter((c) => (c?.score_breakdown?.replay ?? 0) >= 50).length;

  return (
    <figure className="mb-4">
      <figcaption className="flex items-baseline justify-between gap-2 mb-1.5">
        <span className="readout text-muted">most replayed · clips marked</span>
        <span className="readout text-muted">{onPeak} of {clips.length} on a replay peak</span>
      </figcaption>
      <div
        className="relative h-14 rounded-input bg-paper3 overflow-hidden"
        role="img"
        aria-label={`Replay curve of the source video, ${fmt(D)} long, with ${clips.length} clips marked`}
      >
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 w-full h-full text-ink2" aria-hidden="true">
          <path d={area} className="fill-current opacity-25" />
        </svg>
        {clips.map((c, i) => (
          <div
            key={i}
            className={`absolute inset-y-0 border-x ${tone(c.predicted_score ?? 0)}`}
            style={{ left: pct(c.start), width: `max(2px, calc(${pct(c.end)} - ${pct(c.start)}))` }}
            title={`Clip ${i + 1} · ${fmt(c.start)}–${fmt(c.end)} · score ${c.predicted_score ?? '–'}`}
          >
            <span className="absolute top-0.5 left-1 font-mono text-micro">{i + 1}</span>
          </div>
        ))}
      </div>
      <div className="flex justify-between readout text-muted mt-1">
        <span>0:00</span>
        <span>{fmt(D)}</span>
      </div>
    </figure>
  );
}
