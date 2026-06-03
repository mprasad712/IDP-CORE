type AppConfig = Record<string, string | undefined>;

declare global {
  interface Window {
    __APP_CONFIG__?: AppConfig;
  }
}

export function getRuntimeEnv(key: string): string | undefined {
  const runtimeValue =
    typeof window !== "undefined" ? window.__APP_CONFIG__?.[key] : undefined;
  if (runtimeValue && String(runtimeValue).trim()) {
    return String(runtimeValue).trim();
  }
  const processValue = (process.env as Record<string, string | undefined>)?.[key];
  if (processValue && String(processValue).trim()) {
    return String(processValue).trim();
  }
  return undefined;
}
