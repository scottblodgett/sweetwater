// /api/ops/* -> OPS_API_URL/ops/*. The four paths the API has; anything else is 404 here, not a probe upstream.
import { forward } from "@/lib/proxy";

export const dynamic = "force-dynamic";

const GET_PATHS = new Set(["incidents", "report", "stream", "gate"]);

type Ctx = { params: Promise<{ path: string[] }> };

function notFound(what: string): Response {
  return Response.json({ error: { code: "NOT_FOUND", message: `no route ${what}`, details: {} } }, { status: 404 });
}

export async function GET(req: Request, ctx: Ctx): Promise<Response> {
  const leaf = (await ctx.params).path.join("/");
  if (!GET_PATHS.has(leaf)) return notFound(`GET /ops/${leaf}`);
  return forward(req, `/ops/${leaf}`);
}

export async function POST(req: Request, ctx: Ctx): Promise<Response> {
  const leaf = (await ctx.params).path.join("/");
  if (leaf !== "gate") return notFound(`POST /ops/${leaf}`);
  return forward(req, "/ops/gate", { auth: true });
}
