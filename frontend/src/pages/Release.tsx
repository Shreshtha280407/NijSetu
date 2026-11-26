import {
  Check,
  ClipboardCheck,
  Download,
  Eye,
  LockKeyhole,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { Badge, EmptyState, InfoNote, PageHeading, Stat } from '../components/UI';
import { useWorkspace } from '../Workspace';

export default function Release() {
  const { analyzed, reviewed, run, downloadRelease } = useWorkspace();
  const result = run?.result;
  const ready = !!result?.release_ready;
  const downloadable = ready && reviewed;
  return (
    <>
      <PageHeading
        eyebrow="The last gate matters"
        title="Confidence before every release."
        description="Actual sanitization and validation results from the Python engine."
        action={
          <Badge tone={downloadable ? 'success' : 'warning'}>
            <LockKeyhole size={14} />
            {downloadable ? 'Validated release ready' : 'Release locked'}
          </Badge>
        }
      />
      {!analyzed ? (
        <EmptyState
          title="Let’s prepare a release worth trusting."
          description="Run a demo or uploaded dataset, then review the engine’s results."
          action={
            <Link to="/dashboard/intake" className="button button-primary">
              Go to batch intake
            </Link>
          }
        />
      ) : (
        <>
          <section className={`release-verdict ${downloadable ? 'release-ready' : ''}`}>
            <span className="verdict-icon">
              {downloadable ? <ShieldCheck size={35} /> : <LockKeyhole size={35} />}
            </span>
            <div>
              <div className="eyebrow">{result?.report.status ?? 'BLOCKED'}</div>
              <h2>
                {!ready
                  ? 'Publication blocked by the engine.'
                  : reviewed
                    ? 'Protected identity. Preserved medicine.'
                    : 'Review required before download.'}
              </h2>
              <p>
                {!ready
                  ? result?.report.reason_codes?.join(' · ') || 'No validated release is available.'
                  : `${result?.report.counts?.records_released ?? 0} records released. Held and unsupported inputs remain excluded.`}
              </p>
            </div>
            {!reviewed && (
              <Link to="/dashboard/review" className="button">
                <Eye size={17} />
                Review batch results
              </Link>
            )}
          </section>
          <div className="stats-grid">
            <Stat
              icon={ShieldCheck}
              label="Released records"
              value={String(result?.report.counts?.records_released ?? 0)}
              detail="From the sanitization report"
            />
            <Stat
              icon={TriangleAlert}
              label="Held records"
              value={String(result?.report.counts?.records_held ?? 0)}
              detail="Excluded from publication"
            />
            <Stat
              icon={ClipboardCheck}
              label="Files released"
              value={String(result?.report.counts?.files_processed ?? 0)}
              detail="Sanitized output files"
            />
            <Stat
              icon={ShieldCheck}
              label="Policy"
              value={run?.policy ?? ''}
              detail="Applied by Python"
              accent
            />
          </div>
          <div className="release-layout">
            <section className="raised checks-panel">
              <div className="panel-heading">
                <div>
                  <h2>Every check, in the open</h2>
                  <p>Engine checks against the actual release.</p>
                </div>
                <ClipboardCheck size={22} />
              </div>
              {result?.checks.map((check) => (
                <div className="release-check" key={check.name}>
                  <ShieldCheck size={20} />
                  <div>
                    <strong>{check.name.replaceAll('_', ' ')}</strong>
                    <p>{check.detail}</p>
                  </div>
                  <Badge tone={check.passed ? 'success' : 'warning'}>
                    {check.passed ? (
                      <>
                        <Check size={12} /> Passed
                      </>
                    ) : (
                      'Blocked'
                    )}
                  </Badge>
                </div>
              ))}
              <div className="release-check">
                <Eye size={20} />
                <div>
                  <strong>Operator review</strong>
                  <p>Review the records and exclusions.</p>
                </div>
                <Badge tone={reviewed ? 'success' : 'warning'}>
                  {reviewed ? 'Reviewed' : 'Pending'}
                </Badge>
              </div>
            </section>
            <section className="raised preservation-panel">
              <div className="panel-heading">
                <div>
                  <h2>Applied transformations</h2>
                  <p>Counts from the engine, including retained clinical values.</p>
                </div>
              </div>
              {Object.entries(result?.report.actions?.by_kind ?? {}).map(([action, count]) => (
                <div className="clinical-row" key={action}>
                  <span>{action.replaceAll('_', ' ')}</span>
                  <strong>{count}</strong>
                </div>
              ))}
              <div className="privacy-summary inset">
                <h3>Release archive</h3>
                <p>
                  Sanitized CSV, TSV, JSON, FHIR or TXT files, report.json, hold_index.json and
                  manifest.json. Original inputs and private analysis are excluded.
                </p>
              </div>
            </section>
          </div>
          <section className="review-decision">
            <span className="decision-icon">
              <Download size={27} />
            </span>
            <div className="decision-copy">
              <h2>
                {downloadable ? 'Your checked release is ready.' : 'Complete the release gates.'}
              </h2>
              <p>
                Download is checked again by the server for review status and release integrity.
              </p>
            </div>
            <button
              className="button button-primary"
              disabled={!downloadable}
              onClick={downloadRelease}
            >
              <Download size={18} />
              Download validated release
            </button>
          </section>
          {result?.validation_scope && <InfoNote>{result.validation_scope}</InfoNote>}
        </>
      )}
    </>
  );
}
