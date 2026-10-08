export function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  return normalized === "localhost" || normalized === "127.0.0.1" || normalized === "::1";
}

export function buildArtifactUrl(
  servePath: string,
  port: number | null | undefined,
  hostname = typeof location !== "undefined" ? location.hostname : "",
): string | null {
  if (!port || !isLoopbackHostname(hostname)) return null;
  const path = servePath.startsWith("/") ? servePath : `/${servePath}`;
  return `http://127.0.0.1:${port}${path}`;
}
