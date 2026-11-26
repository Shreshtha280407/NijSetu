import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import {
  sampleFiles,
  policies as fallbackPolicies,
  type BatchFile,
  type PolicyName,
} from './data/demo';
import { api, encodeFile, type EngineRun, type Options, type Policies } from './api';

type Run = EngineRun & { date: string };
type Workspace = {
  files: BatchFile[];
  sample: boolean;
  policy: PolicyName;
  policies: Policies;
  connected: boolean;
  running: boolean;
  progress: number;
  stage: string;
  error: string;
  analyzed: boolean;
  reviewed: boolean;
  run: EngineRun | null;
  history: Run[];
  sourceSystem: string;
  setSourceSystem: (value: string) => void;
  notice: string;
  setNotice: (message: string) => void;
  loadSample: () => void;
  clearFiles: () => void;
  addFiles: (files: FileList | File[]) => void;
  removeFile: (id: string) => void;
  setPolicy: (policy: PolicyName) => void;
  startRun: () => Promise<void>;
  confirmReview: () => Promise<void>;
  downloadRelease: () => Promise<void>;
};
const WorkspaceContext = createContext<Workspace | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [files, setFiles] = useState<BatchFile[]>(sampleFiles);
  const [sample, setSample] = useState(true);
  const [policy, updatePolicy] = useState<PolicyName>('balanced');
  const [policies, setPolicies] = useState<Policies>(fallbackPolicies);
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState('');
  const [error, setError] = useState('');
  const [run, setRun] = useState<EngineRun | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [notice, setNotice] = useState('');
  const [sourceSystem, updateSourceSystem] = useState('');
  const generation = useRef(0);
  const fileObjects = useRef(new Map<string, File>());
  const demoFiles = useRef(sampleFiles);
  const analyzed = run?.state === 'complete';
  const reviewed = !!run?.reviewed;

  useEffect(() => {
    let active = true;
    api<Options>('/options')
      .then((options) => {
        if (!active) return;
        setConnected(true);
        setPolicies(options.policies);
        demoFiles.current = options.demo_files;
        setFiles((previous) => (previous === sampleFiles ? options.demo_files : previous));
      })
      .catch(() => {
        if (active) setConnected(false);
      });
    return () => {
      active = false;
      generation.current++;
    };
  }, []);
  useEffect(() => {
    if (!notice) return;
    const timeout = setTimeout(() => setNotice(''), 5500);
    return () => clearTimeout(timeout);
  }, [notice]);

  function invalidate() {
    generation.current++;
    setRunning(false);
    setProgress(0);
    setRun(null);
    setError('');
  }
  async function startRun() {
    if (running || !files.length) return;
    invalidate();
    const version = generation.current;
    setRunning(true);
    setStage('Reading selected files');
    try {
      const options = await api<Options>('/options');
      if (version !== generation.current) return;
      setConnected(true);
      setPolicies(options.policies);
      let payload: object;
      if (sample) payload = { demo: true, demo_files: files.map((file) => file.name), policy };
      else {
        if (
          files.length > options.max_files ||
          files.reduce((sum, f) => sum + f.size, 0) > options.max_bytes
        ) {
          throw new Error('Choose up to 500 files with a total size of 32 MiB.');
        }
        const encoded = [];
        for (const entry of files) {
          const file = fileObjects.current.get(entry.id);
          if (!file) throw new Error('Please select your files again.');
          encoded.push({ name: entry.name, content: await encodeFile(file) });
        }
        payload = { files: encoded, policy, source_system: sourceSystem };
      }
      if (version !== generation.current) return;
      let current = await api<EngineRun>('/runs', payload);
      while (version === generation.current) {
        setRun(current);
        setProgress(current.progress);
        setStage(current.stage);
        if (current.state === 'complete' || current.state === 'failed') break;
        await new Promise((resolve) => setTimeout(resolve, 350));
        if (version !== generation.current) return;
        current = await api<EngineRun>(`/runs/${current.id}`);
      }
      if (version !== generation.current) return;
      if (current.state === 'failed') throw new Error(current.error || 'Engine processing failed.');
      setHistory((previous) => [{ ...current, date: new Date().toISOString() }, ...previous]);
      setNotice(
        current.result?.release_ready
          ? 'Engine processing complete. Review the results before downloading.'
          : 'Run complete. The engine blocked publication; inspect the reported reasons.',
      );
    } catch (e) {
      if (version === generation.current) {
        setError(e instanceof Error ? e.message : 'Processing failed. Please try again.');
        setRun(null);
      }
    } finally {
      if (version === generation.current) setRunning(false);
    }
  }
  async function confirmReview() {
    if (!analyzed || !run || reviewed) return;
    const version = generation.current;
    try {
      const updated = await api<EngineRun>(`/runs/${run.id}/review`, { acknowledged: true });
      if (version !== generation.current) return;
      setRun(updated);
      setHistory((previous) =>
        previous.map((item) => (item.id === updated.id ? { ...item, reviewed: true } : item)),
      );
      setNotice('Review recorded. Sanitization holds remain excluded from the release.');
    } catch (e) {
      setNotice(e instanceof Error ? e.message : 'Could not save review.');
    }
  }
  async function downloadRelease() {
    if (!run?.reviewed || !run.result?.release_ready) return;
    try {
      const response = await fetch(`/api/runs/${run.id}/download`);
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error.replaceAll('_', ' '));
      }
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `nijsetu-${run.id}.zip`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice('Validated release downloaded, including the manifest and hold report.');
    } catch (e) {
      setNotice(e instanceof Error ? e.message : 'Download failed. Try again.');
    }
  }
  const value: Workspace = {
    files,
    sample,
    policy,
    policies,
    connected,
    running,
    progress,
    stage,
    error,
    analyzed,
    reviewed,
    run,
    history,
    notice,
    setNotice,
    sourceSystem,
    setSourceSystem(value) {
      if (running) return;
      invalidate();
      updateSourceSystem(value);
    },
    loadSample() {
      if (running) return;
      invalidate();
      fileObjects.current.clear();
      setFiles(demoFiles.current);
      setSample(true);
      setNotice('Demo data loaded. Run it through the Python engine.');
    },
    clearFiles() {
      if (running) return;
      invalidate();
      fileObjects.current.clear();
      setFiles([]);
      setSample(false);
    },
    addFiles(incoming) {
      if (running) return;
      invalidate();
      if (sample) fileObjects.current.clear();
      const added = Array.from(incoming).map((file) => {
        const id = crypto.randomUUID();
        const name = file.webkitRelativePath || file.name;
        const format = name.split('.').pop()?.toUpperCase() || 'UNKNOWN';
        fileObjects.current.set(id, file);
        return {
          id,
          name,
          size: file.size,
          format,
          supported: ['CSV', 'TSV', 'JSON', 'TXT', 'ZIP'].includes(format),
        };
      });
      setFiles((previous) => [...(sample ? [] : previous), ...added]);
      setSample(false);
      setNotice('Files queued. Run the batch to upload them to the local Python engine.');
    },
    removeFile(id) {
      if (running) return;
      invalidate();
      fileObjects.current.delete(id);
      setFiles((previous) => previous.filter((file) => file.id !== id));
    },
    setPolicy(next) {
      if (next === policy || running) return;
      invalidate();
      updatePolicy(next);
      setNotice('Policy updated. Run the batch again to apply it.');
    },
    startRun,
    confirmReview,
    downloadRelease,
  };
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}
export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error('WorkspaceProvider is required');
  return context;
}
