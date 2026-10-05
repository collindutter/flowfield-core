export const capability: Readonly<{
  version: 1;
  method: "_flowfield/quiesce";
  scope: "native-turns-and-terminals";
}>;

export type Receipt = typeof capability & {
  sessionId: string;
  status: "confirmed" | "uncertain";
  reason: string | null;
  checkedThreads: number;
  stoppedTerminals: number;
};

export class Cleanup {
  constructor(
    rpc: (method: string, params: object) => Promise<unknown>,
    cancel: (sessionId: string) => Promise<unknown>,
    options?: {
      timeoutMs?: number;
      maxThreads?: number;
      maxTerminals?: number;
    },
  );
  observe(message: unknown): void;
  run<T>(operation: () => T | Promise<T>): Promise<T>;
  attach<T>(
    operation: () => T | Promise<T>,
    sessionId?: string | null,
  ): Promise<T>;
  bind<T extends { sessionId: string }>(session: T): T;
  stop(sessionId: string): Promise<Receipt>;
}
