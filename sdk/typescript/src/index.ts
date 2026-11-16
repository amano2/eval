/**
 * EvalKit TypeScript SDK — Non-blocking, failure-tolerant trace client.
 */

export interface Span {
  name: string;
  kind: "model" | "retrieval" | "tool" | "guardrail" | "assertion" | "judge" | "other";
  attributes?: Record<string, any>;
  input?: any;
  output?: any;
  started_at: string;
  ended_at?: string;
  status: "ok" | "error";
  error?: string;
}

export interface Trace {
  type: "trace";
  feature?: string;
  scenario?: string;
  origin?: "real" | "synthetic" | "eval";
  external_id?: string;
  tags?: string[];
  input?: any;
  output?: any;
  model?: string;
  prompt_version?: string;
  app_version?: string;
  status: "ok" | "error";
  error?: string;
  latency_ms?: number;
  started_at: string;
  spans: Span[];
}

export interface EvalKitConfig {
  endpoint?: string;
  apiKey?: string;
  flushIntervalMs?: number;
  maxQueueSize?: number;
}

export class EvalKitClient {
  private endpoint: string;
  private apiKey: string;
  private queue: Trace[] = [];
  private maxQueueSize: number;
  private timer: any;

  constructor(config: EvalKitConfig = {}) {
    this.endpoint = (config.endpoint || process.env.EVALKIT_ENDPOINT || "http://localhost:8000").replace(/\/$/, "") + "/api/ingest";
    this.apiKey = config.apiKey || process.env.EVALKIT_API_KEY || "";
    this.maxQueueSize = config.maxQueueSize || 5000;
    const interval = config.flushIntervalMs || 1000;

    if (typeof setInterval !== "undefined") {
      this.timer = setInterval(() => this.flush(), interval);
      if (this.timer.unref) this.timer.unref();
    }
  }

  public enqueue(trace: Trace): void {
    if (this.queue.length >= this.maxQueueSize) {
      // Drop item to protect host app
      return;
    }
    this.queue.push(trace);
  }

  public async flush(): Promise<void> {
    if (this.queue.length === 0 || !this.apiKey) return;
    const batch = this.queue.splice(0, 50);
    try {
      if (typeof fetch !== "undefined") {
        await fetch(this.endpoint, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-API-Key": this.apiKey
          },
          body: JSON.stringify({ batch })
        });
      }
    } catch {
      // Drop silently: SDK never crashes host app
    }
  }
}

let globalClient: EvalKitClient | null = null;

export function init(config?: EvalKitConfig): EvalKitClient {
  globalClient = new EvalKitClient(config);
  return globalClient;
}

export function getClient(): EvalKitClient {
  if (!globalClient) {
    globalClient = new EvalKitClient();
  }
  return globalClient;
}
