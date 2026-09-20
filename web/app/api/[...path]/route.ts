import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const base = process.env.API_INTERNAL_URL;
  if (!base) return new Response("API_INTERNAL_URL не настроен", { status: 503 });

  const url = new URL(`/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`, base);
  const headers = new Headers(request.headers);
  for (const header of ["host", "connection", "content-length", "transfer-encoding"]) headers.delete(header);

  try {
    const upstream = await fetch(url, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      duplex: "half",
      cache: "no-store",
      redirect: "manual",
    } as RequestInit & { duplex: "half" });
    const responseHeaders = new Headers(upstream.headers);
    for (const header of ["connection", "content-length", "transfer-encoding"]) responseHeaders.delete(header);
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return Response.json({ detail: "API недоступен" }, { status: 502 });
  }
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
