import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { sampleFiles, type BatchFile, type PolicyName } from './data/demo';

type Run = { id: string; date: string; policy: PolicyName; reviewed: boolean };
type Workspace = {
  files: BatchFile[];
  sample: boolean;
  policy: PolicyName;
  running: boolean;
  progress: number;
  analyzed: boolean;
  reviewed: boolean;
  history: Run[];
  notice: string;
  setNotice: (message: string) => void;
  loadSample: () => void;
  clearFiles: () => void;
  addFiles: (files: FileList | File[]) => void;
  removeFile: (id: string) => void;
  setPolicy: (policy: PolicyName) => void;
  startRun: () => void;
  confirmReview: () => void;
};

const WorkspaceContext = createContext<Workspace | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [files, setFiles] = useState<BatchFile[]>(sampleFiles);
  const [sample, setSample] = useState(true);
  const [policy, updatePolicy] = useState<PolicyName>('balanced');
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [analyzed, setAnalyzed] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [history, setHistory] = useState<Run[]>([]);
  const [notice, setNotice] = useState('');
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  function invalidate() {
    if (timer.current) clearInterval(timer.current);
    setRunning(false);
    setProgress(0);
    setAnalyzed(false);
    setReviewed(false);
  }

  useEffect(
    () => () => {
      if (timer.current) clearInterval(timer.current);
    },
    [],
  );
  useEffect(() => {
    if (!notice) return;
    const timeout = setTimeout(() => setNotice(''), 5500);
    return () => clearTimeout(timeout);
  }, [notice]);

  const value: Workspace = {
    files,
    sample,
    policy,
    running,
    progress,
    analyzed,
    reviewed,
    history,
    notice,
    setNotice,
    loadSample() {
      invalidate();
      setFiles(sampleFiles);
      setSample(true);
      setNotice('Synthetic sample loaded. Your workspace is ready.');
    },
    clearFiles() {
      invalidate();
      setFiles([]);
      setSample(false);
    },
    addFiles(incoming) {
      invalidate();
      const added = Array.from(incoming).map((file, index) => {
        const format = file.name.split('.').pop()?.toUpperCase() || 'UNKNOWN';
        return {
          id: `${Date.now()}-${index}-${file.name}`,
          name: file.name,
          size: file.size,
          format,
          supported: ['CSV', 'TSV', 'JSON', 'TXT'].includes(format),
        };
      });
      // Only filename and size are read. File contents never enter application state.
      setFiles((previous) => [...(sample ? [] : previous), ...added]);
      setSample(false);
      setNotice(
        'Files added for intake preview. Processing requires the Python engine connection.',
      );
    },
    removeFile(id) {
      invalidate();
      setFiles((previous) => previous.filter((file) => file.id !== id));
      setSample(false);
    },
    setPolicy(next) {
      if (next === policy) return;
      invalidate();
      updatePolicy(next);
      setNotice(
        `${next === 'balanced' ? 'Balanced' : 'Strict'} policy selected. Run the sample again to apply it.`,
      );
    },
    startRun() {
      if (!sample || running || !files.length) return;
      invalidate();
      setRunning(true);
      let current = 0;
      timer.current = setInterval(() => {
        current += 10;
        setProgress(current);
        if (current >= 100) {
          if (timer.current) clearInterval(timer.current);
          setRunning(false);
          setAnalyzed(true);
          const run = {
            id: `DEMO-${Date.now().toString().slice(-6)}`,
            date: new Date().toISOString(),
            policy,
            reviewed: false,
          };
          setHistory((previous) => [run, ...previous]);
          setNotice('Sample run complete. Three linked records and one record to review.');
        }
      }, 320);
    },
    confirmReview() {
      if (!analyzed) return;
      setReviewed(true);
      setHistory((previous) =>
        previous.map((run, index) => (index === 0 ? { ...run, reviewed: true } : run)),
      );
      setNotice('Name-only record excluded. The synthetic preview is ready.');
    },
  };

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error('WorkspaceProvider is required');
  return context;
}
