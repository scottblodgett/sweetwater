export function hms(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(11, 19) + "Z";
}

export function usd(n: number | null | undefined): string {
  return `$${(n ?? 0).toFixed(n && n >= 1 ? 2 : 3)}`;
}

export function num(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString("en-US");
}

export function age(seconds: number): string {
  if (seconds < 90) return `${seconds}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

export function shortRun(runId: string): string {
  return runId.length > 8 ? runId.slice(0, 8) : runId;
}
