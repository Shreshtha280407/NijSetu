import { useRef, useState } from 'react';
import {
  Check,
  ChevronRight,
  File,
  FileCheck2,
  FileJson2,
  FileSpreadsheet,
  Files,
  FileText,
  FlaskConical,
  FolderOpen,
  Link2,
  LoaderCircle,
  Play,
  Plus,
  RotateCcw,
  ShieldCheck,
  Trash2,
  TriangleAlert,
  Upload,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { Badge, InfoNote, PageHeading, Stat } from '../components/UI';
import { formatSize, type BatchFile } from '../data/demo';
import { useWorkspace } from '../Workspace';

export default function Intake() {
  const {
    files,
    policies,
    sourceSystem,
    setSourceSystem,
    run,
    stage,
    error,
    sample,
    policy,
    setPolicy,
    loadSample,
    clearFiles,
    addFiles,
    removeFile,
    running,
    progress,
    analyzed,
    startRun,
  } = useWorkspace();
  const [dragging, setDragging] = useState(false);
  const [filter, setFilter] = useState('all');
  const input = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const fileResult = (file: BatchFile) =>
    run?.result?.files.find((item) => item.file_path === file.name);
  const isReady = (file: BatchFile) =>
    fileResult(file) ? fileResult(file)!.disposition === 'PROCESSED' : file.supported;
  const ready = files.filter(isReady).length;
  const quarantined = files.length - ready;
  const visibleFiles = files.filter(
    (file) => filter === 'all' || (filter === 'ready' ? isReady(file) : !isReady(file)),
  );

  return (
    <>
      <PageHeading
        eyebrow="Start with the source"
        title="Good data. Safer beginnings."
        description="Bring your records together. We’ll take care of the person behind them."
        action={
          <button className="button button-small" onClick={loadSample} disabled={running}>
            <FlaskConical size={17} /> Load sample batch
          </button>
        }
      />
      <div className="stats-grid">
        <Stat
          icon={Files}
          label="Files in batch"
          value={files.length.toString().padStart(2, '0')}
          detail={sample ? 'Synthetic sample batch' : 'Your local intake queue'}
        />
        <Stat
          icon={FileCheck2}
          label="Ready to process"
          value={ready.toString().padStart(2, '0')}
          detail={
            analyzed ? 'Engine parsing results' : 'Extensions checked; contents checked on run'
          }
        />
        <Stat
          icon={TriangleAlert}
          label="Quarantined"
          value={quarantined.toString().padStart(2, '0')}
          detail="Kept outside the pipeline"
        />
        <Stat
          icon={ShieldCheck}
          label="Privacy policy"
          value={policies[policy].label}
          detail="Applied across the entire batch"
          accent
        />
      </div>
      <div className="intake-layout">
        <section className="intake-panel raised">
          <div className="panel-heading">
            <div>
              <h2>Your batch</h2>
              <p>A clear view of what goes in.</p>
            </div>
            <Badge tone={sample ? 'indigo' : 'neutral'}>
              {sample ? 'Synthetic sample' : 'Local files'}
            </Badge>
          </div>
          <input
            ref={input}
            type="file"
            multiple
            className="visually-hidden"
            aria-label="Choose batch files"
            disabled={running}
            onChange={(event) => {
              if (event.target.files?.length) addFiles(event.target.files);
              event.target.value = '';
            }}
          />
          <input
            ref={folderInput}
            type="file"
            multiple
            webkitdirectory=""
            className="visually-hidden"
            aria-label="Choose dataset folder"
            disabled={running}
            onChange={(event) => {
              if (event.target.files?.length) addFiles(event.target.files);
              event.target.value = '';
            }}
          />
          <button
            className="button button-small"
            onClick={() => folderInput.current?.click()}
            disabled={running}
          >
            Choose dataset folder
          </button>
          <button
            className={`dropzone inset ${dragging ? 'dropzone-active' : ''}`}
            disabled={running}
            onClick={() => input.current?.click()}
            onDragOver={(event) => {
              event.preventDefault();
              if (!running) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              if (!running && event.dataTransfer.files.length) addFiles(event.dataTransfer.files);
            }}
          >
            <span className="upload-icon raised-small">
              <Upload size={25} />
            </span>
            <strong>
              Drop your files here, or <span>browse files</span>
            </strong>
            <span>CSV, TSV, JSON, TXT, ZIP · FHIR R4 as JSON</span>
            <small>Processed by your local Python engine · up to 32 MiB / 500 files</small>
          </button>
          <div className="file-toolbar">
            <div className="segmented" aria-label="Filter batch files">
              {[
                ['all', `All files (${files.length})`],
                ['ready', `Ready (${ready})`],
                ['quarantined', `Quarantined (${quarantined})`],
              ].map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setFilter(value)}
                  className={filter === value ? 'selected' : ''}
                  aria-pressed={filter === value}
                >
                  {label}
                </button>
              ))}
            </div>
            {files.length > 0 && (
              <button className="text-button muted" disabled={running} onClick={clearFiles}>
                Clear batch
              </button>
            )}
          </div>
          <div className="file-list" aria-live="polite">
            {visibleFiles.map((file) => {
              const outcome = fileResult(file);
              const supported = isReady(file);
              const Icon = ['CSV', 'TSV'].includes(file.format)
                ? FileSpreadsheet
                : file.format === 'JSON'
                  ? FileJson2
                  : file.format === 'TXT'
                    ? FileText
                    : File;
              return (
                <div className={`file-row ${supported ? '' : 'file-quarantined'}`} key={file.id}>
                  <span className={`file-icon ${supported ? '' : 'file-icon-warning'}`}>
                    <Icon size={21} />
                  </span>
                  <div className="file-name">
                    <strong>{file.name}</strong>
                    <span>
                      {file.format} · {formatSize(file.size)}
                      {outcome?.reason_codes.length
                        ? ` · ${outcome.reason_codes.join(', ')}`
                        : !file.supported
                          ? ' · Unsupported format'
                          : ''}
                    </span>
                  </div>
                  <Badge tone={supported ? 'success' : 'warning'}>
                    {outcome ? (
                      outcome.disposition
                    ) : supported ? (
                      <>
                        <Check size={12} /> Ready
                      </>
                    ) : (
                      <>
                        <TriangleAlert size={12} /> Quarantined
                      </>
                    )}
                  </Badge>
                  <button
                    className="icon-button"
                    onClick={() => removeFile(file.id)}
                    disabled={running}
                    aria-label={`Remove ${file.name}`}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              );
            })}
            {!visibleFiles.length && (
              <div className="file-empty">
                <FolderOpen size={28} />
                <strong>
                  {files.length ? 'No files in this view' : 'A fresh start for your records'}
                </strong>
                <span>
                  {files.length
                    ? 'Choose another filter to see your files.'
                    : 'Add files above or load the synthetic sample batch.'}
                </span>
              </div>
            )}
          </div>
          <div className="file-list-footer">
            <LockNote />
            <button
              className="text-button"
              onClick={() => input.current?.click()}
              disabled={running}
            >
              <Plus size={15} /> Add files
            </button>
          </div>
        </section>
        <aside className="intake-aside">
          <section className="policy-card raised">
            <div className="panel-heading">
              <div>
                <h2>Protection, your way</h2>
                <p>Choose a privacy preset.</p>
              </div>
              <ShieldCheck size={21} />
            </div>
            <div className="preset-options">
              {(['balanced', 'strict'] as const).map((name) => (
                <label
                  className={`preset-option ${policy === name ? 'preset-selected' : ''}`}
                  key={name}
                >
                  <input
                    type="radio"
                    name="intake-policy"
                    value={name}
                    checked={policy === name}
                    onChange={() => setPolicy(name)}
                    disabled={running}
                  />
                  <div>
                    <strong>
                      {policies[name].label}
                      {name === 'balanced' && <span>Recommended</span>}
                    </strong>
                    <p>{policies[name].description}</p>
                  </div>
                </label>
              ))}
            </div>
            <dl className="policy-details">
              <div>
                <dt>Age generalization</dt>
                <dd>{policies[policy].age}</dd>
              </div>
              <div>
                <dt>Date shifting</dt>
                <dd>{policies[policy].dates}</dd>
              </div>
              <div>
                <dt>Geography</dt>
                <dd>{policies[policy].geography}</dd>
              </div>
            </dl>
            <Link to="/dashboard/policy" className="text-link">
              View the full policy <ChevronRight size={15} />
            </Link>
          </section>
          <section className="dark-note">
            <span className="dark-note-icon">
              <Link2 size={23} />
            </span>
            <h3>
              Many files.
              <br />
              One patient story.
            </h3>
            <p>
              Scoped identifiers and cross-script aliases bring records together. A name alone never
              makes a match.
            </p>
            <div>
              <Check size={15} /> Evidence before every link
            </div>
          </section>
        </aside>
      </div>
      {!sample && files.length > 0 && (
        <InfoNote>
          Files are uploaded only when you run the batch. ZIP contents are expanded and checked by
          the engine. Unsupported and malformed inputs receive explicit holds or quarantine reasons.
        </InfoNote>
      )}
      {!sample && (
        <section className="raised policy-card source-system-card">
          <label>
            Source system / hospital (optional)
            <input
              aria-label="Source system"
              value={sourceSystem}
              maxLength={120}
              disabled={running}
              placeholder="e.g. HOSPITAL_A"
              onChange={(event) => setSourceSystem(event.target.value)}
            />
          </label>
          <p>
            Set this only when all files share the same identifier issuer. Leave blank for
            independent sources; explicit FHIR issuers still apply.
          </p>
        </section>
      )}
      {error && (
        <div className="review-decision" role="alert">
          <TriangleAlert size={24} />
          <p>{error}</p>
        </div>
      )}
      <section className={`run-panel ${analyzed ? 'run-panel-done' : ''}`} aria-live="polite">
        <div className="run-panel-copy">
          <span className="run-icon">
            {running ? (
              <LoaderCircle size={24} className="spin" />
            ) : analyzed ? (
              <Check size={24} />
            ) : (
              <ShieldCheck size={24} />
            )}
          </span>
          <div>
            <strong>
              {running
                ? stage
                : analyzed
                  ? 'Engine run complete. Ready for a closer look.'
                  : 'A safer release starts with a thoughtful check.'}
            </strong>
            <p>
              {running
                ? `Python engine · ${progress}% complete`
                : analyzed
                  ? `${run?.result?.analysis.counts.documents_processed ?? 0} records analyzed · ${run?.result?.report.counts?.records_released ?? 0} released · ${run?.result?.report.status ?? 'BLOCKED'}`
                  : 'We’ll find identities, link the evidence, and prepare your review.'}
            </p>
          </div>
        </div>
        {analyzed ? (
          <Link to="/dashboard/review" className="button button-primary">
            Review patient links <Link2 size={17} />
          </Link>
        ) : (
          <button
            className="button button-primary"
            disabled={running || !files.length}
            onClick={startRun}
          >
            {running ? <LoaderCircle size={17} className="spin" /> : <Play size={17} />}{' '}
            {running ? 'Processing batch…' : sample ? 'Run sample batch' : 'Run uploaded batch'}
          </button>
        )}
        {running && (
          <div className="run-progress">
            <span style={{ width: `${progress}%` }} />
          </div>
        )}
      </section>
      {analyzed && (
        <div className="rerun">
          <button className="text-button muted" onClick={startRun}>
            <RotateCcw size={14} /> Run the batch again
          </button>
        </div>
      )}
    </>
  );
}

function LockNote() {
  return (
    <span className="file-lock-note">
      <ShieldCheck size={14} /> Unsupported files are kept out, with a reason.
    </span>
  );
}
