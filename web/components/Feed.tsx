"use client";
// The verdict feed: /ops/incidents newest first. What the agents decided about the ranch, not what the sensors said.
import { AGENTS, STATUSES, SUBJECT_TYPES, type Incident, type IncidentStatus } from "@/lib/types";
import { hms } from "@/lib/format";

export interface FeedFilters { status: IncidentStatus | ""; owner: string; subject_type: string }

interface Props { incidents: Incident[]; count: number | null; filters: FeedFilters; onFilters: (f: FeedFilters) => void; error: string | null }

export function Feed({ incidents, count, filters, onFilters, error }: Props) {
  const set = (patch: Partial<FeedFilters>) => onFilters({ ...filters, ...patch });
  return (
    <>
      <div className="filters">
        <label>status
          <select value={filters.status} onChange={(e) => set({ status: e.target.value as FeedFilters["status"] })}>
            <option value="">all</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label>owner
          <select value={filters.owner} onChange={(e) => set({ owner: e.target.value })}>
            <option value="">all</option>
            {AGENTS.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </label>
        <label>subject
          <select value={filters.subject_type} onChange={(e) => set({ subject_type: e.target.value })}>
            <option value="">all</option>
            {SUBJECT_TYPES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <span className="dim filters-count">{count === null ? "" : `${count} shown`}</span>
      </div>
      {error ? <div className="banner">{error}</div> : null}
      <div className="feed" role="table" aria-label="incidents">
        {incidents.length === 0 && !error ? <div className="dim">no incidents match</div> : null}
        {incidents.map((inc) => (
          <div className="row" key={inc.id ?? `${inc.incident_key}@${inc.first_seen_at}`} role="row" title={`${inc.location} · ${inc.category} · seen ${inc.occurrences}× · opened tick ${inc.tick_opened}, last tick ${inc.tick_last_seen}`}>
            <span className={`tag sev-${inc.severity}`}>{inc.severity}</span>
            <span className={`tag st-${inc.status}`}>{inc.status}</span>
            <span className="id">{inc.incident_key}</span>
            <span className="owner">{inc.owner ?? <span className="dim">unrouted</span>}</span>
            <span className="why">{inc.summary}{inc.last_value != null ? <span className="dim"> · {inc.last_value}{inc.unit ? ` ${inc.unit}` : ""}</span> : null}</span>
            <span className="when dim">{hms(inc.last_seen_at)}</span>
          </div>
        ))}
      </div>
    </>
  );
}
