"use client";
// The token gauge, one point per tick off /ops/stream. Two charts on one x-axis rather than a
// dual-axis chart: tokens and dollars are different measures and never share a scale.
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Tick } from "@/lib/types";
import { hms, num, shortRun, usd } from "@/lib/format";

const COLOR = { input: "#0969da", output: "#bf3989", cost: "#8250df", grid: "#eaeef2", axis: "#d0d7de", label: "#656d76" };

interface Point { id: number; label: string; tick: number; run: string; at: string; input: number; output: number; cost: number; error: string | null }

function toPoints(ticks: Tick[]): Point[] {
  return ticks.map((t) => ({ id: t.id, label: `#${t.tick}`, tick: t.tick, run: shortRun(t.run_id), at: t.at, input: t.fields.input_tokens ?? 0, output: t.fields.output_tokens ?? 0, cost: t.cost_usd ?? 0, error: t.error }));
}

interface TipProps { active?: boolean; payload?: Array<{ payload: Point }>; kind: "tokens" | "cost" }
function Tip({ active, payload, kind }: TipProps) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="tip">
      <div className="tip-head">tick {p.tick} <span className="dim">· run {p.run} · {hms(p.at)}</span></div>
      {kind === "tokens" ? (
        <>
          <div><span className="swatch" style={{ background: COLOR.input }} />input <b>{num(p.input)}</b></div>
          <div><span className="swatch" style={{ background: COLOR.output }} />output <b>{num(p.output)}</b></div>
        </>
      ) : (
        <div><span className="swatch" style={{ background: COLOR.cost }} />cost <b>{usd(p.cost)}</b></div>
      )}
      {p.error ? <div className="tip-error">tick failed: {p.error}</div> : null}
    </div>
  );
}

const axisStyle = { fontSize: 10, fill: COLOR.label, fontFamily: "inherit" };

export function Gauge({ ticks }: { ticks: Tick[] }) {
  const points = toPoints(ticks);
  const total = points.reduce((s, p) => s + p.cost, 0);
  const latest = points.at(-1);
  return (
    <>
      <div className="gauge-row">
        <div className="chart-title">tokens per tick <span className="dim">input and output, the measurement</span></div>
        <div className="chart">
          <ResponsiveContainer width="100%" height={170}>
            <LineChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={COLOR.grid} vertical={false} />
              <XAxis dataKey="label" tick={axisStyle} stroke={COLOR.axis} tickLine={false} minTickGap={24} />
              <YAxis tick={axisStyle} stroke={COLOR.axis} tickLine={false} axisLine={false} width={56} tickFormatter={(v: number) => num(v)} />
              <Tooltip content={<Tip kind="tokens" />} cursor={{ stroke: COLOR.axis }} />
              <Legend verticalAlign="top" align="right" height={20} iconType="plainline" wrapperStyle={{ fontSize: 11 }} />
              <Line type="monotone" dataKey="input" name="input tokens" stroke={COLOR.input} strokeWidth={2} dot={{ r: 3, strokeWidth: 0, fill: COLOR.input }} activeDot={{ r: 5 }} isAnimationActive={false} />
              <Line type="monotone" dataKey="output" name="output tokens" stroke={COLOR.output} strokeWidth={2} dot={{ r: 3, strokeWidth: 0, fill: COLOR.output }} activeDot={{ r: 5 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="gauge-row">
        <div className="chart-title">cost per tick <span className="dim">USD at the list price in routing.PRICE_TABLE</span></div>
        <div className="chart">
          <ResponsiveContainer width="100%" height={120}>
            <LineChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={COLOR.grid} vertical={false} />
              <XAxis dataKey="label" tick={axisStyle} stroke={COLOR.axis} tickLine={false} minTickGap={24} />
              <YAxis tick={axisStyle} stroke={COLOR.axis} tickLine={false} axisLine={false} width={56} tickFormatter={(v: number) => usd(v)} />
              <Tooltip content={<Tip kind="cost" />} cursor={{ stroke: COLOR.axis }} />
              <Line type="monotone" dataKey="cost" name="cost" stroke={COLOR.cost} strokeWidth={2} dot={{ r: 3, strokeWidth: 0, fill: COLOR.cost }} activeDot={{ r: 5 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="legend">
        <span>{points.length ? `${points.length} tick${points.length === 1 ? "" : "s"} on screen` : "waiting for the first tick…"}</span>
        {latest ? <span className="dim">latest #{latest.tick} · {num(latest.input + latest.output)} tok · {usd(latest.cost)}</span> : null}
        <span className="cumulative">seen this session: {usd(total)}</span>
      </div>
    </>
  );
}
