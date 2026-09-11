import { forward } from "@/lib/proxy";

export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<Response> {
  return forward(req, "/health");
}
