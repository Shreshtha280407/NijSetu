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
import { sampleRecords } from '../data/demo';
import { useWorkspace } from '../Workspace';

export default function Review() {
  const { analyzed, reviewed, confirmReview } = useWorkspace();
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState('registration');
  const [acknowledged, setAcknowledged] = useState(false);
  const record = sampleRecords.find((item) => item.id === selected)!;
  const visible = sampleRecords.filter(
    (item) =>
      (filter === 'all' || (filter === 'linked' ? item.linked : !item.linked)) &&
      `${item.name} ${item.file} ${item.tag}`.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <>
      <PageHeading
        eyebrow="Follow the evidence"
        title="One person. A clearer picture."
        description="Understand every connection, and give uncertain records the care they need."
        action={
          analyzed ? (
            <Badge tone={reviewed ? 'success' : 'warning'}>
              {reviewed ? (
                <>
                  <CheckCheck size={14} /> Review complete
                </>
              ) : (
                <>
                  <TriangleAlert size={14} /> 1 decision needed
                </>
              )}
            </Badge>
          ) : undefined
        }
      />
      {!analyzed ? (
        <EmptyState
          title="Every good link starts with a batch."
          description="Run the synthetic batch from intake to see the patient links, their evidence, and the record held for review."
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
              value="01"
              detail="One consistent pseudonym"
            />
            <Stat
              icon={Link2}
              label="Records linked"
              value="03"
              detail="Corroborated across sources"
            />
            <Stat
              icon={TriangleAlert}
              label="Held for review"
              value="01"
              detail={reviewed ? 'Excluded from the preview' : 'Name-only match · no anchor'}
            />
            <Stat
              icon={ShieldCheck}
              label="Clinical values"
              value="Kept"
              detail="Illustrative sample comparison"
              accent
            />
          </div>
          <div className="review-layout">
            <section className="raised review-record-panel">
              <div className="panel-heading">
                <div>
                  <h2>The patient story</h2>
                  <p>Four records. Every connection explained.</p>
                </div>
                <Badge tone="indigo">Patient NS-07</Badge>
              </div>
              <div className="review-toolbar">
                <div className="segmented" aria-label="Filter records">
                  {[
                    ['all', 'All records'],
                    ['linked', 'Linked (3)'],
                    ['held', 'Held (1)'],
                  ].map(([value, label]) => (
                    <button
                      className={filter === value ? 'selected' : ''}
                      aria-pressed={filter === value}
                      key={value}
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
                    className={`review-record ${selected === item.id ? 'review-record-selected' : ''}`}
                    aria-pressed={selected === item.id}
                  >
                    <span className={`record-avatar ${item.linked ? '' : 'record-avatar-held'}`}>
                      {item.linked ? <FileText size={21} /> : <TriangleAlert size={21} />}
                    </span>
                    <span className="review-record-copy">
                      <strong lang={item.id === 'consultation' ? 'hi' : undefined}>
                        {item.name}
                      </strong>
                      <span>{item.file}</span>
                      <small>{item.type}</small>
                    </span>
                    <Badge tone={item.linked ? 'success' : 'warning'}>
                      {item.linked ? (
                        <>
                          <Check size={12} /> Linked
                        </>
                      ) : reviewed ? (
                        'Excluded'
                      ) : (
                        'Held'
                      )}
                    </Badge>
                  </button>
                ))}
                {visible.length === 0 && (
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
              <div className="record-panel-footer">
                <Link2 size={15} /> Select a record to see the reasoning behind it.
              </div>
            </section>
            <aside className="evidence-panel raised">
              <div className="panel-heading">
                <div>
                  <div className="eyebrow">Why this record?</div>
                  <h2>Evidence, made visible.</h2>
                </div>
              </div>
              <div className={`evidence-verdict ${record.linked ? '' : 'evidence-verdict-held'}`}>
                {record.linked ? <Link2 size={23} /> : <TriangleAlert size={23} />}
                <div>
                  <strong>
                    {record.linked ? 'A supported connection' : 'A name is not enough'}
                  </strong>
                  <span>
                    {record.linked
                      ? 'Linked to Patient NS-07'
                      : 'No merge · held outside the patient group'}
                  </span>
                </div>
              </div>
              <div className="evidence-source">
                <span>Selected source</span>
                <strong>{record.file}</strong>
              </div>
              <div className="evidence-tags">
                <span>{record.tag}</span>
                {record.id === 'consultation' && <span>DEVANAGARI → LATIN</span>}
              </div>
              <p className="evidence-explanation">
                {record.evidence}.{' '}
                {record.linked
                  ? 'The sample illustrates corroborated linkage, rather than a name-only match.'
                  : 'Without an independent identifier, this record cannot safely join the patient story.'}
              </p>
              <div className="evidence-line">
                <span className="evidence-node" />
                <div>
                  <strong>
                    {record.linked
                      ? 'Identity evidence agrees'
                      : 'Corroborating identifier missing'}
                  </strong>
                  <p>
                    {record.linked
                      ? 'A scoped reference supports the name alias.'
                      : 'No shared scoped UHID or other independent anchor.'}
                  </p>
                </div>
              </div>
              <div className="evidence-line">
                <span className="evidence-node" />
                <div>
                  <strong>
                    {record.linked ? 'One stable patient token' : 'Conservative by design'}
                  </strong>
                  <p>
                    {record.linked
                      ? 'The same pseudonym follows all three linked sources.'
                      : 'An uncertain link stays out of the release preview.'}
                  </p>
                </div>
              </div>
              <InfoNote>
                Illustrative evidence from the built-in sample. Engine confidence scores are not
                simulated.
              </InfoNote>
            </aside>
          </div>
          <section className={`review-decision ${reviewed ? 'decision-complete' : ''}`}>
            <span className="decision-icon">
              {reviewed ? <ShieldCheck size={27} /> : <TriangleAlert size={27} />}
            </span>
            <div className="decision-copy">
              <h2>
                {reviewed
                  ? 'A careful decision. A safer preview.'
                  : 'One record needs a little more care.'}
              </h2>
              <p>
                {reviewed
                  ? 'The name-only OPD note is excluded. The three corroborated records remain linked.'
                  : '“Arif” in opd_note.txt has no corroborating identifier. Keep it out of this release.'}
              </p>
              {!reviewed && (
                <label className="acknowledgement">
                  <input
                    type="checkbox"
                    checked={acknowledged}
                    onChange={(event) => setAcknowledged(event.target.checked)}
                  />
                  I’ve reviewed the evidence. Exclude the name-only record.
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
                onClick={confirmReview}
                disabled={!acknowledged}
              >
                Confirm exclusion <Check size={17} />
              </button>
            )}
          </section>
        </>
      )}
    </>
  );
}
