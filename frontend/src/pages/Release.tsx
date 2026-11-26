import {
  Check,
  CheckCheck,
  ClipboardCheck,
  Download,
  Eye,
  FileJson2,
  Fingerprint,
  Link2,
  LockKeyhole,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { Badge, CheckItem, EmptyState, InfoNote, PageHeading } from '../components/UI';
import { downloadJson, policies } from '../data/demo';
import { useWorkspace } from '../Workspace';

export default function Release() {
  const { analyzed, reviewed, policy, setNotice } = useWorkspace();
  function downloadPreview() {
    if (!analyzed || !reviewed) return;
    downloadJson('nijsetu-synthetic-preview.json', {
      demo: true,
      scope:
        'Illustrative frontend preview. Not processed or validated by the NijSetu Python engine.',
      policy,
      patient: {
        token: 'NS-07',
        age_band: policy === 'balanced' ? '40-49' : '40-59',
        geography: policy === 'balanced' ? 'Synthetic district' : null,
      },
      clinical_values: {
        blood_pressure: '140/90 mmHg',
        hba1c: '7.2%',
        medication: 'Metformin 500 mg',
        frequency: 'Twice daily',
        length_of_stay: '3 days',
      },
      linked_sources: ['registration.csv', 'consultation.json', 'discharge.txt'],
      excluded_sources: [
        { file: 'opd_note.txt', reason: 'NAME_ONLY_NO_ANCHOR' },
        { file: 'scan_0042.pdf', reason: 'UNSUPPORTED_FORMAT' },
      ],
    });
    setNotice(
      'Synthetic preview downloaded. This file is an illustration, not an engine-validated release.',
    );
  }
  return (
    <>
      <PageHeading
        eyebrow="The last gate matters"
        title="Confidence before every release."
        description="See what’s protected, what’s preserved, and exactly what’s ready to share."
        action={
          <Badge tone={reviewed ? 'success' : 'warning'}>
            {reviewed ? (
              <>
                <CheckCheck size={14} /> Demo checks complete
              </>
            ) : (
              <>
                <LockKeyhole size={14} /> Preview locked
              </>
            )}
          </Badge>
        }
      />
      {!analyzed ? (
        <EmptyState
          title="Let’s prepare a release worth trusting."
          description="Start with the synthetic batch, review the patient links, then explore the release checks and the protected output."
          action={
            <Link to="/dashboard/intake" className="button button-primary">
              Go to batch intake
            </Link>
          }
        />
      ) : (
        <>
          <section className={`release-verdict ${reviewed ? 'release-ready' : ''}`}>
            <span className="verdict-icon">
              {reviewed ? <ShieldCheck size={35} /> : <LockKeyhole size={35} />}
            </span>
            <div>
              <div className="eyebrow">
                {reviewed ? 'Synthetic preview ready' : 'Review required'}
              </div>
              <h2>
                {reviewed
                  ? 'Protected identity. Preserved medicine.'
                  : 'One open decision. Nothing released.'}
              </h2>
              <p>
                {reviewed
                  ? 'Three linked records are included. The uncertain record stays excluded.'
                  : 'The name-only record needs your review before the sample can be downloaded.'}
              </p>
            </div>
            {!reviewed && (
              <Link to="/dashboard/review" className="button">
                <Eye size={17} /> Review the held record
              </Link>
            )}
          </section>
          <div className="release-layout">
            <section className="raised checks-panel">
              <div className="panel-heading">
                <div>
                  <h2>Every check, in the open</h2>
                  <p>Illustrative checks for the synthetic sample.</p>
                </div>
                <ClipboardCheck size={22} />
              </div>
              {[
                {
                  icon: Fingerprint,
                  title: 'Identifier protection',
                  description: 'Sample names and direct identifiers replaced.',
                  passed: true,
                },
                {
                  icon: Link2,
                  title: 'Reference consistency',
                  description: 'One sample patient token across three sources.',
                  passed: true,
                },
                {
                  icon: FileJson2,
                  title: 'Output structure',
                  description: 'Synthetic preview follows the displayed structure.',
                  passed: true,
                },
                {
                  icon: CheckCheck,
                  title: 'Clinical preservation',
                  description: 'Illustrative clinical values remain unchanged.',
                  passed: true,
                },
                {
                  icon: ShieldCheck,
                  title: 'Uncertain-record review',
                  description: reviewed
                    ? 'Name-only record reviewed and excluded.'
                    : 'Name-only OPD record awaits a decision.',
                  passed: reviewed,
                },
              ].map(({ icon: Icon, title, description, passed }) => (
                <div className="release-check" key={title}>
                  <span className="check-row-icon">
                    <Icon size={20} />
                  </span>
                  <div>
                    <strong>{title}</strong>
                    <p>{description}</p>
                  </div>
                  <Badge tone={passed ? 'success' : 'warning'}>
                    {passed ? (
                      <>
                        <Check size={12} />{' '}
                        {title === 'Uncertain-record review' ? 'Reviewed' : 'Demo pass'}
                      </>
                    ) : (
                      'Pending'
                    )}
                  </Badge>
                </div>
              ))}
              <div className="checks-note">
                <TriangleAlert size={17} />
                <span>
                  These demonstrate the interface. Actual leak scans and integrity validation will
                  run through the Python engine.
                </span>
              </div>
            </section>
            <section className="raised preservation-panel">
              <div className="panel-heading">
                <div>
                  <h2>The medicine stays.</h2>
                  <p>Source → synthetic preview</p>
                </div>
                <span className="small-icon-well">
                  <CheckCheck size={21} />
                </span>
              </div>
              <div className="clinical-table">
                <div className="clinical-table-head">
                  <span>Clinical value</span>
                  <span>Preserved</span>
                </div>
                {[
                  ['Blood pressure', '140/90 mmHg'],
                  ['HbA1c', '7.2%'],
                  ['Medication', 'Metformin 500 mg'],
                  ['Frequency', 'Twice daily'],
                  ['Length of stay', '3 days'],
                ].map(([label, value]) => (
                  <div className="clinical-row" key={label}>
                    <span>{label}</span>
                    <strong>
                      <Check size={14} />
                      {value}
                    </strong>
                  </div>
                ))}
              </div>
              <div className="privacy-summary inset">
                <h3>Identity, thoughtfully protected.</h3>
                <CheckItem>Names replaced with a patient token</CheckItem>
                <CheckItem>Age generalized to {policies[policy].age.toLowerCase()}</CheckItem>
                <CheckItem>{policies[policy].geography}</CheckItem>
                <CheckItem>Uncertain record kept out</CheckItem>
              </div>
            </section>
          </div>
          <section className="export-panel raised">
            <div className="export-copy">
              <span className="export-icon inset">
                <FileJson2 size={27} />
              </span>
              <div>
                <h2>Your synthetic preview</h2>
                <p>JSON · Patient NS-07 · {policies[policy].label} policy</p>
                <span>A small, readable example of the protected output.</span>
              </div>
            </div>
            <button
              className="button button-primary"
              onClick={downloadPreview}
              disabled={!reviewed}
            >
              <Download size={18} /> Download synthetic preview
            </button>
          </section>
          <InfoNote>
            Frontend demo only. No real patient data is processed, no engine validation is claimed,
            and the downloaded file is explicitly marked as synthetic.
          </InfoNote>
        </>
      )}
    </>
  );
}
