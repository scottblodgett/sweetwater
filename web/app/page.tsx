"use client";
// The window. One EventSource on /api/ops/stream is the clock: every tick that lands refreshes the
// feed, the report, and the gate, all of which read sw_ops through the proxy. Nothing here can
// spend a token or fire a sweep; the loop is the only thing that talks to the ranch.
import { useCallback, useEffect, useRef, useState } from "react";
import { Gauge } from "@/components/Gauge";
import { Feed, type FeedFilters } from "@/components/Feed";
import { Rails } from "@/components/Rails";
import { Summary } from "@/components/Summary";
import { Gate, type Answered } from "@/components/Gate";
import { MapPlaceholder } from "@/components/MapPlaceholder";
import { OpsError, describe, getJson } from "@/lib/client";
import type { Collection, Incident, PendingWrite, ShiftReport, Single, Tick } from "@/lib/types";
import { hms } from "@/lib/format";

const MAX_TICKS = 120;
const GATE_POLL_MS = 20_000;

type Conn = "connecting" | "live" | "reconnecting";

export default function Window() {
  const [ticks, setTicks] = useState<Tick[]>([]);
  const [conn, setConn] = useState<Conn>("connecting");
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [incidentCount, setIncidentCount] = useState<number | null>(null);
  const [filters, setFilters] = useState<FeedFilters>({ status: "", owner: "", subject_type: "" });
  const [feedError, setFeedError] = useState<string | null>(null);
  const [report, setReport] = useState<ShiftReport | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingWrite[]>([]);
  const [gateError, setGateError] = useState<string | null>(null);
  const [answered, setAnswered] = useState<Answered[]>([]);
  const filtersRef = useRef(filters);
  const loadedOnce = useRef(false);

  const loadIncidents = useCallback(async () => {
    const f = filtersRef.current;
    const q = new URLSearchParams({ limit: "100" });
    if (f.status) q.set("status", f.status);
    if (f.owner) q.set("owner", f.owner);
    if (f.subject_type) q.set("subject_type", f.subject_type);
    try {
      const out = await getJson<Collection<Incident>>(`/api/ops/incidents?${q}`);
      setIncidents(out.data); setIncidentCount(out.meta.count); setFeedError(null);
    } catch (err) { setFeedError(describe(err)); }
  }, []);

  const loadReport = useCallback(async () => {
    try {
      const out = await getJson<Single<ShiftReport>>("/api/ops/report");
      setReport(out.data); setReportError(null);
    } catch (err) {
      if (err instanceof OpsError && err.status === 404) { setReport(null); setReportError(null); } else setReportError(describe(err));
    }
  }, []);

  const loadGate = useCallback(async () => {
    try {
      const out = await getJson<Collection<PendingWrite>>("/api/ops/gate");
      setPending(out.data); setGateError(null);
    } catch (err) { setGateError(describe(err)); }
  }, []);

  const refreshAll = useCallback(() => { void loadIncidents(); void loadReport(); void loadGate(); }, [loadIncidents, loadReport, loadGate]);

  // the clock: the SSE stream, proxied. EventSource reconnects on its own and resends Last-Event-ID.
  // The first load of the other panels rides on the stream's open (or its first failure, so a dead API
  // shows up as a sentence in every panel rather than a blank page).
  useEffect(() => {
    const es = new EventSource("/api/ops/stream");
    const firstLoad = () => { if (!loadedOnce.current) { loadedOnce.current = true; refreshAll(); } };
    es.onopen = () => { setConn("live"); firstLoad(); };
    es.onerror = () => { setConn("reconnecting"); firstLoad(); };
    es.addEventListener("tick", (e: MessageEvent<string>) => {
      try {
        const t = JSON.parse(e.data) as Tick;
        setTicks((prev) => (prev.some((p) => p.id === t.id) ? prev : [...prev, t].sort((a, b) => a.id - b.id).slice(-MAX_TICKS)));
        refreshAll();
      } catch { /* a malformed frame is dropped; the next tick is a fresh one */ }
    });
    return () => es.close();
  }, [refreshAll]);

  // a decision made at the CLI does not land a tick, so the gate list also polls
  useEffect(() => { const h = setInterval(() => void loadGate(), GATE_POLL_MS); return () => clearInterval(h); }, [loadGate]);

  const onFilters = (f: FeedFilters) => { filtersRef.current = f; setFilters(f); void loadIncidents(); };
  const latest = ticks.at(-1) ?? null;
  const onAnswered = (a: Answered) => { setAnswered((prev) => [...prev.filter((x) => x.audit_id !== a.audit_id), a]); void loadGate(); };

  return (
    <>
      <header>
        <h1>Sweetwater <span className="dim">· ops window</span></h1>
        <div className={`status ${conn === "live" ? "running" : conn === "reconnecting" ? "error" : ""}`}>
          {conn === "live" ? "live" : conn}{latest ? <span className="dim"> · tick #{latest.tick} at {hms(latest.at)} · {latest.store}</span> : null}
        </div>
      </header>
      <main>
        <section id="gauge-panel" className="panel hero">
          <div className="panel-title">TOKEN GAUGE <span className="dim">one point per tick, from /ops/stream</span></div>
          <Gauge ticks={ticks} />
        </section>
        <section id="rails-panel" className="panel">
          <div className="panel-title">RAILS <span className="dim">{latest ? `tick #${latest.tick}` : ""}</span></div>
          <Rails tick={latest} pendingWrites={pending.length} />
        </section>
        <section id="summary-panel" className="panel">
          <div className="panel-title">SHIFT REPORT <span className="dim">/ops/report</span></div>
          <Summary report={report} error={reportError} />
        </section>
        <section id="gate-panel" className="panel">
          <div className="panel-title">GATE <span className="dim">writes proposed, waiting on a human</span></div>
          <Gate pending={pending} answered={answered} onAnswered={onAnswered} error={gateError} />
        </section>
        <section id="verdict-panel" className="panel">
          <div className="panel-title">INCIDENTS <span className="dim">/ops/incidents, newest first</span></div>
          <Feed incidents={incidents} count={incidentCount} filters={filters} onFilters={onFilters} error={feedError} />
        </section>
        <section id="map-panel" className="panel">
          <div className="panel-title">RANCH MAP</div>
          <MapPlaceholder />
        </section>
      </main>
    </>
  );
}
