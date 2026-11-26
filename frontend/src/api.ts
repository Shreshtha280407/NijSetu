import type { BatchFile, PolicyName } from './data/demo';

export type Policies = Record<
  PolicyName,
  {
    label: string;
    description: string;
    age: string;
    dates: string;
    geography: string;
    cohort: string;
    providers: string;
    facilities: string;
  }
>;
export type EngineRecord = {
  id: string;
  name: string;
  file: string;
  type: string;
  status: string;
  cluster: string | null;
  reasons: string[];
  features: { role: string; tier: string; rules: string[] }[];
};
export type EngineRun = {
  id: string;
  state: 'queued' | 'running' | 'complete' | 'failed';
  progress: number;
  stage: string;
  policy: PolicyName;
  demo: boolean;
  reviewed: boolean;
  file_count: number;
  error?: string;
  result?: {
    records: EngineRecord[];
    analysis: { counts: Record<string, number>; status: string };
    files: {
      file_path: string;
      disposition: string;
      record_count: number | null;
      reason_codes: string[];
    }[];
    ingest_holds: { file_path: string; reason_codes: string[] }[];
    holds: { file_no?: number; record_no?: number; reason_codes: string[]; disposition: string }[];
    report: {
      status: string;
      counts?: Record<string, number>;
      reason_codes?: string[];
      hold_summary?: Record<string, number>;
      actions?: { by_kind: Record<string, number> };
      leak_scan?: { clean: boolean };
    };
    checks: { name: string; passed: boolean; detail: string }[];
    validation_scope: string;
    release_ready: boolean;
  };
};

export async function api<T>(path: string, payload?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(
      `/api${path}`,
      payload === undefined
        ? undefined
        : {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          },
    );
  } catch {
    throw new Error('Cannot reach the Python engine. Start python -m nijsetu.web and try again.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok || !data)
    throw new Error(
      data?.error?.replaceAll('_', ' ') ||
        'Cannot reach the Python engine. Start python -m nijsetu.web and try again.',
    );
  return data as T;
}

export function encodeFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`Could not read ${file.name}. Select it again.`));
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.readAsDataURL(file);
  });
}

export type Options = {
  policies: Policies;
  demo_files: BatchFile[];
  max_bytes: number;
  max_files: number;
};
