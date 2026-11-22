// gta-clipper: what the composite score is made of, and the cutscene flag.
// Fields come from youtube_signals.score_clips (score_breakdown, flags, signals).

const PARTS = [
  ['replay', 'replay', 'YouTube viewers rewound this stretch (Most replayed)'],
  ['reaction', 'reactions', 'Commentator discovery phrases ("no way", "what is that"...)'],
  ['ai', 'ai', "Claude's own judgement of the moment"],
  ['cutscene', 'cutscene', 'A cutscene with the commentator around it'],
  ['action', 'action', 'Loud moments, against the video’s own loudness'],
];

const tone = (v) => (v >= 80 ? 'text-ok' : v >= 50 ? 'text-brass' : 'text-ink2');

export default function ScoreBreakdown({ clip }) {
  const parts = clip.score_breakdown;
  const mostlyCutscene = (clip.flags || []).includes('mostly_cutscene');
  if (!parts && !mostlyCutscene) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 mb-2" aria-label="score breakdown">
      {mostlyCutscene && (
        <span
          className="badge-warn"
          title="Over 70% of this clip is a letterboxed cutscene: mostly Rockstar's footage, the kind that draws copyright claims. Keep the stretch where the commentator talks."
        >
          mostly cutscene{Number.isFinite(clip.signals?.cutscene_pct) ? ` ${clip.signals.cutscene_pct}%` : ''}
        </span>
      )}
      {parts && PARTS.filter(([key]) => key in parts).map(([key, label, hint]) => (
        <span key={key} className="readout bg-paper3 px-2 py-0.5 rounded-full" title={hint}>
          <span className="text-muted">{label}</span>{' '}
          <b className={tone(parts[key])}>{parts[key]}</b>
        </span>
      ))}
    </div>
  );
}
