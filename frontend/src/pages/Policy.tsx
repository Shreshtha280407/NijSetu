import { Check, Layers, LockKeyhole, Settings2, ShieldCheck } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Badge, InfoNote, PageHeading } from '../components/UI';
import { policies, type PolicyName } from '../data/demo';
import { useWorkspace } from '../Workspace';

export default function Policy() {
  const { policy, setPolicy, running } = useWorkspace();
  const rows: {
    label: string;
    key: 'age' | 'dates' | 'geography' | 'cohort' | 'providers' | 'facilities';
  }[] = [
    { label: 'Age generalization', key: 'age' },
    { label: 'Per-patient date shift', key: 'dates' },
    { label: 'Coarse geography', key: 'geography' },
    { label: 'Small-group review threshold', key: 'cohort' },
    { label: 'Provider names', key: 'providers' },
    { label: 'Facility names', key: 'facilities' },
  ];
  return (
    <>
      <PageHeading
        eyebrow="A thoughtful balance"
        title="Privacy with a purpose."
        description="Choose how much to generalize, while keeping the clinical story useful."
        action={
          <Badge tone="indigo">
            <Settings2 size={14} /> {policies[policy].label} selected
          </Badge>
        }
      />
      <div className="policy-choices">
        {(Object.keys(policies) as PolicyName[]).map((name) => (
          <label
            key={name}
            className={`policy-choice raised ${policy === name ? 'policy-choice-active' : ''}`}
          >
            <div className="policy-choice-top">
              <span className="icon-well inset">
                {name === 'balanced' ? <Layers size={27} /> : <ShieldCheck size={27} />}
              </span>
              <input
                type="radio"
                name="policy-preset"
                checked={policy === name}
                onChange={() => setPolicy(name)}
                disabled={running}
                value={name}
                aria-label={`${policies[name].label} policy`}
              />
            </div>
            <div className="policy-choice-title">
              <h2>{policies[name].label}</h2>
              {name === 'balanced' && <Badge tone="neutral">Recommended</Badge>}
            </div>
            <p>{policies[name].description}</p>
            <span className="policy-choice-detail">
              {name === 'balanced'
                ? 'A considered starting point for synthetic research data.'
                : 'Stronger generalization, with less contextual detail.'}
            </span>
            <span className="policy-choice-selected">
              {policy === name ? (
                <>
                  <Check size={16} /> Active for this workspace
                </>
              ) : (
                'Select this preset'
              )}
            </span>
          </label>
        ))}
      </div>
      <section className="policy-comparison raised">
        <div className="panel-heading">
          <div>
            <h2>The details behind the protection</h2>
            <p>Presets mirror the existing Python policy configuration.</p>
          </div>
          <ShieldCheck size={23} />
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Protection</th>
                <th scope="col">
                  Balanced {policy === 'balanced' && <span className="table-active">Active</span>}
                </th>
                <th scope="col">
                  Strict {policy === 'strict' && <span className="table-active">Active</span>}
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <th scope="row">{row.label}</th>
                  <td className={policy === 'balanced' ? 'active-column' : ''}>
                    {policies.balanced[row.key]}
                  </td>
                  <td className={policy === 'strict' ? 'active-column' : ''}>
                    {policies.strict[row.key]}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <div className="policy-principles">
        <section className="dark-note">
          <LockKeyhole size={26} />
          <h3>Some protections are always on.</h3>
          <p>
            Direct patient names are masked, contact details are suppressed, and national
            identifiers are removed. Clinical measurements and history are retained under both
            presets.
          </p>
        </section>
        <section className="raised policy-cohort-note">
          <ShieldCheck size={25} />
          <h3>A review trigger, not automatic exclusion.</h3>
          <p>
            Small-group thresholds flag records for review. They do not automatically suppress a
            small cohort. Uncertain patient links remain held regardless of the preset.
          </p>
        </section>
      </div>
      <InfoNote>
        Changes apply to the next sample run and reset any existing review. The production engine
        connection will apply the selected preset to real pipeline requests.
      </InfoNote>
      <div className="page-bottom-action">
        <Link className="button button-primary" to="/dashboard/intake">
          Back to batch intake
        </Link>
      </div>
    </>
  );
}
