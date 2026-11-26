import { useState } from 'react';
import {
  Check,
  CheckCheck,
  FileText,
  Link2,
  Search,
  ShieldCheck,
  TriangleAlert,
  Users,
  X,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { Badge, EmptyState, InfoNote, PageHeading, Stat } from '../components/UI';
import { useWorkspace } from '../Workspace';

export default function Review() {
  const { analyzed, reviewed, confirmReview, run } = useWorkspace();
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState('');
  const [acknowledgedRun, setAcknowledgedRun] = useState('');
  const [saving, setSaving] = useState(false);
  const result = run?.result;
  const records = result?.records ?? [];
  const linked = records.filter((record) => record.status === 'ACCEPTED').length;
  const held = records.filter((record) => record.status === 'HELD').length;
  const groups = result?.report.counts?.patients_total ?? 0;
  const visible = records.filter(
    (item) =>
      (filter === 'all' ||
        (filter === 'linked' ? item.status === 'ACCEPTED' : item.status === 'HELD')) &&
      `${item.name} ${item.file} ${item.reasons.join(' ')}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const record = records.find((item) => item.id === selected) ?? visible[0] ?? records[0];
  return (
    <>
      <PageHeading
        eyebrow="Follow the evidence"
        title="One person. A clearer picture."
        description="Inspect actual engine decisions, patient groups and held records."
        action={
          analyzed ? (
            <Badge tone={reviewed ? 'success' : 'warning'}>
              {reviewed ? <CheckCheck size={14} /> : <TriangleAlert size={14} />}
              {reviewed ? 'Review complete' : 'Review required'}
            </Badge>
          ) : undefined
        }
      />
      {!analyzed ? (
        <EmptyState
          title="Every good link starts with a batch."
          description="Run a demo or uploaded dataset from intake to see the engine’s patient links and holds."
          action={
            <Link className="button button-primary" to="/dashboard/intake">
              Go to batch intake
            </Link>
          }
        />
      ) : (
        <>
          <div className="stats-grid">
            <Stat
              icon={Users}
              label="Patient groups"
              value={String(groups)}
              detail="Patient contexts used for sanitization"
            />
            <Stat
              icon={Link2}
              label="Records linked"
              value={String(linked)}
              detail="Corroborated by the engine"
            />
            <Stat
              icon={TriangleAlert}
              label="Identity holds"
              value={String(held)}
              detail="Unmerged; may be sanitized independently"
            />
            <Stat
              icon={ShieldCheck}
              label="Records released"
              value={String(result?.report.counts?.records_released ?? 0)}
              detail={result?.report.status ?? 'BLOCKED'}
              accent
            />
          </div>
          <div className="review-layout">
            <section className="raised review-record-panel">
              <div className="panel-heading">
                <div>
                  <h2>The patient story</h2>
                  <p>{records.length} parsed records. Every decision from the engine.</p>
                </div>
              </div>
              <div className="review-toolbar">
                <div className="segmented" aria-label="Filter records">
                  {[
                    ['all', 'All records'],
                    ['linked', `Linked (${linked})`],
                    ['held', `Held (${held})`],
                  ].map(([value, label]) => (
                    <button
                      key={value}
                      className={filter === value ? 'selected' : ''}
                      aria-pressed={filter === value}
                      onClick={() => setFilter(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <label className="search-field inset">
                  <Search size={17} />
                  <input
                    placeholder="Find a name or file…"
                    aria-label="Search records"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                  {search && (
                    <button
                      className="icon-button"
                      onClick={() => setSearch('')}
                      aria-label="Clear search"
                    >
                      <X size={14} />
                    </button>
                  )}
                </label>
              </div>
              <div className="review-record-list">
                {visible.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => setSelected(item.id)}
                    className={`review-record ${record?.id === item.id ? 'review-record-selected' : ''}`}
                    aria-pressed={record?.id === item.id}
                  >
                    <span
                      className={`record-avatar ${item.status === 'HELD' ? 'record-avatar-held' : ''}`}
                    >
                      <FileText size={21} />
                    </span>
                    <span className="review-record-copy">
                      <strong>{item.name}</strong>
                      <span>{item.file}</span>
                      <small>
                        {item.type} · record {item.id}
                      </small>
                    </span>
                    <Badge tone={item.status === 'HELD' ? 'warning' : 'success'}>
                      {item.status === 'ACCEPTED'
                        ? 'Linked'
                        : item.status === 'HELD'
                          ? 'Held'
                          : 'Singleton'}
                    </Badge>
                  </button>
                ))}
                {!visible.length && (
                  <div className="file-empty">
                    <Search size={28} />
                    <strong>No matching records</strong>
                    <span>Try another name, filename, or filter.</span>
                    <button
                      className="text-button"
                      onClick={() => {
                        setSearch('');
                        setFilter('all');
                      }}
                    >
                      Clear filters
                    </button>
                  </div>
                )}
              </div>
            </section>
            <aside className="evidence-panel raised">
              <div className="panel-heading">
                <div>
                  <div className="eyebrow">Why this record?</div>
                  <h2>Evidence, made visible.</h2>
                </div>
              </div>
              {record ? (
                <>
                  <div
                    className={`evidence-verdict ${record.status === 'HELD' ? 'evidence-verdict-held' : ''}`}
                  >
                    <Link2 size={23} />
                    <div>
                      <strong>
                        {record.status === 'ACCEPTED'
                          ? 'A supported connection'
                          : record.status === 'HELD'
                            ? 'Insufficient or conflicting evidence'
                            : 'Independent patient record'}
                      </strong>
                      <span>
                        {record.status === 'HELD'
                          ? 'Not merged with other patient records'
                          : record.cluster
                            ? `Patient group ${record.cluster}`
                            : 'Outside resolved patient groups'}
                      </span>
                    </div>
                  </div>
                  <div className="evidence-source">
                    <span>Selected source</span>
                    <strong>{record.file}</strong>
                  </div>
                  <div className="evidence-tags">
                    {record.reasons.map((reason) => (
                      <span key={reason}>{reason}</span>
                    ))}
                  </div>
                  <p className="evidence-explanation">
                    {record.reasons.length
                      ? record.reasons.join(' · ')
                      : 'No cross-record link was accepted. This record is processed independently if the sanitization checks allow it.'}
                  </p>
                  {record.features.map((feature, index) => (
                    <div className="evidence-line" key={index}>
                      <span className="evidence-node" />
                      <div>
                        <strong>{feature.role.replaceAll('_', ' ')}</strong>
                        <p>
                          {feature.tier} · {feature.rules.join(', ')}
                        </p>
                      </div>
                    </div>
                  ))}
                </>
              ) : (
                <p>No records could be parsed. Inspect file-level reasons below.</p>
              )}
              <InfoNote>
                Private source evidence is shown for local review. The downloaded release contains
                sanitized output and aggregate reports.
              </InfoNote>
            </aside>
          </div>
          <section className="raised checks-panel">
            <div className="panel-heading">
              <div>
                <h2>File accounting and exclusions</h2>
                <p>Parsing dispositions and sanitization holds are separate engine decisions.</p>
              </div>
            </div>
            {result?.files.map((file) => (
              <div className="release-check" key={file.file_path}>
                <FileText size={20} />
                <div>
                  <strong>{file.file_path}</strong>
                  <p>
                    {file.record_count ?? 'Unknown'} records ·{' '}
                    {file.reason_codes.join(', ') || 'Parsed successfully'}
                  </p>
                </div>
                <Badge tone={file.disposition === 'PROCESSED' ? 'success' : 'warning'}>
                  {file.disposition}
                </Badge>
              </div>
            ))}
            {result?.ingest_holds.map((hold, i) => (
              <p key={i}>
                {hold.file_path}: {hold.reason_codes.join(', ')}
              </p>
            ))}
            {Object.entries(result?.report.hold_summary ?? {}).map(([reason, count]) => (
              <p key={reason}>
                <strong>{reason}</strong> · {count}
              </p>
            ))}
            {result?.report.reason_codes?.map((reason) => (
              <p key={reason} role="alert">
                {reason}
              </p>
            ))}
          </section>
          <section className={`review-decision ${reviewed ? 'decision-complete' : ''}`}>
            <span className="decision-icon">
              {reviewed ? <ShieldCheck size={27} /> : <TriangleAlert size={27} />}
            </span>
            <div className="decision-copy">
              <h2>{reviewed ? 'Review recorded.' : 'Review the batch before release.'}</h2>
              <p>
                Identity holds are not merged; the engine may sanitize them independently. Release
                holds and quarantined files are excluded. Acknowledging this review does not
                override a blocked release.
              </p>
              {!reviewed && (
                <label className="acknowledgement">
                  <input
                    type="checkbox"
                    checked={acknowledgedRun === run?.id}
                    onChange={(event) => setAcknowledgedRun(event.target.checked ? run!.id : '')}
                  />
                  I’ve reviewed the records and all exclusions.
                </label>
              )}
            </div>
            {reviewed ? (
              <Link to="/dashboard/release" className="button button-primary">
                View release checks <ShieldCheck size={17} />
              </Link>
            ) : (
              <button
                className="button button-primary"
                disabled={acknowledgedRun !== run?.id || saving}
                onClick={async () => {
                  setSaving(true);
                  try {
                    await confirmReview();
                  } finally {
                    setSaving(false);
                  }
                }}
              >
                Confirm review <Check size={17} />
              </button>
            )}
          </section>
        </>
      )}
    </>
  );
}
