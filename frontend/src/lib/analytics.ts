import {
  ANALYTICS_EVENT_SCHEMAS,
  type AnalyticsEventName,
  type AnalyticsEventPayloads,
} from "./analyticsEvents";

// Amplitude behaviour analytics (mvp.md AA-28). Talks to Amplitude's HTTP API
// v2 directly (https://www.docs.developers.amplitude.com/analytics/apis/http-v2-api/)
// rather than pulling in the @amplitude/analytics-browser SDK — this repo's
// unattended-run environments have no network to `npm install` a new
// dependency (CLAUDE.md), and a same-origin-free fire-and-forget POST is all
// three documented events (Observability.md) need.
const AMPLITUDE_ENDPOINT = "https://api2.amplitude.com/2/httpapi";
const DEVICE_ID_STORAGE_KEY = "aa_analytics_device_id";

// Defense in depth alongside the AnalyticsEventPayloads types above and the
// CI gate in scripts/check-analytics-schema.mjs (keep this list in sync with
// that script's own copy): refuses to send anything that looks like a
// financial value or account identifier even if a payload reaches here via
// an `as` cast that bypasses the type system.
const FORBIDDEN_FIELD_PATTERNS = [
  /amount/i,
  /ticker/i,
  /account/i,
  /balance/i,
  /price/i,
  /quantity/i,
  /symbol/i,
  /cusip/i,
  /isin/i,
];

function isSchemaSafe(event: AnalyticsEventName, payload: Record<string, unknown>): boolean {
  const allowedFields: readonly string[] = ANALYTICS_EVENT_SCHEMAS[event];
  return Object.keys(payload).every(
    (key) => allowedFields.includes(key) && !FORBIDDEN_FIELD_PATTERNS.some((pattern) => pattern.test(key)),
  );
}

let cachedDeviceId: string | null = null;

function getDeviceId(): string {
  if (cachedDeviceId) return cachedDeviceId;
  const stored = window.localStorage.getItem(DEVICE_ID_STORAGE_KEY);
  const deviceId = stored ?? crypto.randomUUID();
  if (!stored) window.localStorage.setItem(DEVICE_ID_STORAGE_KEY, deviceId);
  cachedDeviceId = deviceId;
  return deviceId;
}

// Fires a behaviour-only Amplitude event. No-ops (rather than throwing) when
// VITE_AMPLITUDE_API_KEY isn't set — every dev/CI/sandbox environment in this
// repo runs without it (same convention as AA-16/AA-21's "no creds here"
// LLM/price fetchers) — and when the payload fails the schema-safety check,
// so a bug in analytics code can never break the product it's instrumenting.
export function track<E extends AnalyticsEventName>(event: E, payload: AnalyticsEventPayloads[E]): void {
  if (!isSchemaSafe(event, payload as unknown as Record<string, unknown>)) {
    if (import.meta.env.DEV) {
      console.error(`analytics: refusing to send "${event}" — payload contains a disallowed field`);
    }
    return;
  }

  const apiKey = import.meta.env.VITE_AMPLITUDE_API_KEY;
  if (!apiKey) return;

  const body = JSON.stringify({
    api_key: apiKey,
    events: [
      {
        event_type: event,
        device_id: getDeviceId(),
        event_properties: payload,
        time: Date.now(),
      },
    ],
  });

  void fetch(AMPLITUDE_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  }).catch(() => {
    // Analytics must never break the product — swallow network errors.
  });
}
