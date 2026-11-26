import { useEffect, useState } from 'react';
import {
  Activity,
  Check,
  ChevronRight,
  FileCheck2,
  Globe2,
  Layers,
  Link2,
  LockKeyhole,
  Menu,
  Pause,
  Play,
  ShieldCheck,
  TriangleAlert,
  X,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { Badge, Brand } from '../components/UI';
import { sampleRecords } from '../data/demo';

const features = [
  {
    icon: Globe2,
    title: 'Multilingual detection',
    description: 'English, Hindi and Hinglish names found even with no registration anchor.',
  },
  {
    icon: Link2,
    title: 'Patient linking',
    description:
      'One pseudonym per patient across CSV, JSON, notes and FHIR. Abstains when unsure.',
  },
  {
    icon: ShieldCheck,
    title: 'Privacy policy',
    description: 'Age bands, district geography and shifted dates, with small-group risk reviewed.',
  },
  {
    icon: Activity,
    title: 'Clinical integrity',
    description: 'BP, HbA1c, doses and stay length are checked against the source after export.',
  },
  {
    icon: Layers,
    title: 'FHIR-aware',
    description: 'IDs, fullUrls and references are rewritten together, then resolved again.',
  },
  {
    icon: TriangleAlert,
    title: 'Fail-closed export',
    description: 'One leaked identifier in any path blocks the release. Nothing downloads.',
  },
];
const steps = [
  {
    title: 'Ingest',
    description:
      'CSV, TSV, JSON, TXT and FHIR R4. Unsupported files are quarantined with a reason.',
  },
  {
    title: 'Link',
    description: 'Scoped IDs and cross-script names, merged only when the evidence agrees.',
  },
  {
    title: 'Validate',
    description: 'Leak scan, reference check and clinical values compared with the source.',
  },
  {
    title: 'Release',
    description: 'Atomic publish when it passes. A blocked run publishes nothing.',
  },
];
const trust = [
  { title: 'Runs fully offline', description: 'No cloud API and no patient data transfer.' },
  { title: 'CPU only', description: 'No generative model on the runtime path.' },
  { title: 'Loopback interface', description: 'The local preview binds to 127.0.0.1 only.' },
  { title: 'Synthetic data only', description: 'Competition fixtures, never real patients.' },
];
const statuses = [
  'Reading 4 files…',
  'Scanning registration.csv',
  'Transliterating Devanagari to Latin',
  'Normalising “Md.” to Mohammad',
  'Arif: name only, no anchor',
  'Matching scoped UHID and alias…',
  '3 records agree · 1 abstains',
  'Merging into one patient token',
  'Merged 3 records into Patient NS-07',
  '1 name-only record held for review',
  '1 name-only record held for review',
  'Reading 4 files…',
];

function LiveDemo() {
  const [step, setStep] = useState(5);
  const [paused, setPaused] = useState(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  );
  useEffect(() => {
    if (paused) return;
    const timer = setInterval(() => setStep((previous) => (previous + 1) % 12), 1100);
    return () => clearInterval(timer);
  }, [paused]);
  const effectiveStep = step === 11 ? 0 : step;

  return (
    <div className="live-demo raised">
      <div className="live-demo-top">
        <Badge tone="success" dot>
          LIVE DEMO
        </Badge>
        <span className="demo-status">{statuses[step]}</span>
        <div className="demo-progress inset" aria-hidden="true">
          <span style={{ width: `${(step / 11) * 100}%` }} />
        </div>
        <button
          className="icon-button"
          onClick={() => setPaused(!paused)}
          aria-label={paused ? 'Play demonstration' : 'Pause demonstration'}
        >
          {paused ? <Play size={16} /> : <Pause size={16} />}
        </button>
      </div>
      <div className="demo-flow">
        <div className="demo-records">
          {sampleRecords.map((record, index) => {
            const state =
              effectiveStep >= 5
                ? record.linked
                  ? 'linked'
                  : 'held'
                : effectiveStep > 0 && index === effectiveStep - 1
                  ? 'reading'
                  : 'idle';
            return (
              <div key={record.id} className={`demo-record demo-record-${state}`}>
                <div>
                  <strong lang={index === 1 ? 'hi' : undefined}>{record.name}</strong>
                  <span>{record.file}</span>
                </div>
                <Badge
                  tone={state === 'linked' ? 'success' : state === 'held' ? 'warning' : 'neutral'}
                >
                  {state === 'linked' ? (
                    <>
                      <Check size={12} /> Match
                    </>
                  ) : state === 'held' ? (
                    '! Name only'
                  ) : state === 'reading' ? (
                    'Reading…'
                  ) : (
                    'Ready'
                  )}
                </Badge>
              </div>
            );
          })}
        </div>
        <div className="demo-connector">
          <span className="raised-small">
            <ChevronRight size={22} />
          </span>
        </div>
        <div className="demo-output inset">
          {effectiveStep < 7 ? (
            <div className="demo-wait">
              <Link2 size={28} />
              <span>One patient token appears here</span>
              <small>Evidence first. Identity protected.</small>
            </div>
          ) : (
            <div className="merged-patient raised-small">
              <div className="patient-token">
                <ShieldCheck size={20} /> Patient NS-07
              </div>
              <strong>3 sources linked: CSV, JSON, discharge note</strong>
              <div className="evidence-tags">
                <span>SCOPED_REF</span>
                <span>SCRIPT_ALIAS</span>
              </div>
              <p>
                One pseudonym across every file.
                <br />
                BP 140/90 kept.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function Landing() {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className="landing-page">
      <header className="landing-header raised">
        <Brand />
        <nav
          className={menuOpen ? 'landing-nav landing-nav-open' : 'landing-nav'}
          aria-label="Main navigation"
        >
          {[
            ['Home', '#home'],
            ['How it works', '#how'],
            ['Features', '#features'],
            ['Trust and privacy', '#trust'],
            ['Dashboard', '#dashboard'],
          ].map(([label, href]) => (
            <a key={href} href={href} onClick={() => setMenuOpen(false)}>
              {label}
            </a>
          ))}
        </nav>
        <div className="landing-header-actions">
          <span className="offline-pill inset">
            <span className="status-dot" />
            Offline
          </span>
          <Link to="/dashboard/intake" className="button button-primary button-small">
            Launch dashboard
          </Link>
          <button
            className="icon-button landing-menu"
            onClick={() => setMenuOpen(!menuOpen)}
            aria-label={menuOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X size={22} /> : <Menu size={22} />}
          </button>
        </div>
      </header>
      <main id="main-content">
        <section id="home" className="landing-hero">
          <div className="saffron-dots" aria-hidden="true" />
          <div className="hero-eyebrow inset">Project RAKSHA · Healthcare privacy firewall</div>
          <h1>
            Many records. <span>One patient.</span>
            <br className="hero-linebreak" /> Safely shared.
          </h1>
          <p className="hero-description">
            Every record holds a piece of the story. NijSetu joins them, protects the person, and
            keeps the medicine.
          </p>
          <div className="hero-actions">
            <Link to="/dashboard/intake" className="button button-primary button-large">
              Launch dashboard
            </Link>
            <a href="#how" className="button button-large">
              See how it works
            </a>
          </div>
          <p className="hero-note">No account needed. Use demo data or upload your own dataset.</p>
          <LiveDemo />
        </section>
        <section id="how" className="landing-section">
          <div className="section-heading">
            <div className="eyebrow">How it works</div>
            <h2>One batch, four gates</h2>
          </div>
          <div className="gates-grid">
            {steps.map((step, index) => (
              <div className="gate" key={step.title}>
                <span className="gate-number raised-small">{index + 1}</span>
                <h3>{step.title}</h3>
                <p>{step.description}</p>
              </div>
            ))}
          </div>
        </section>
        <section id="features" className="landing-section">
          <div className="section-heading">
            <div className="eyebrow">Features</div>
            <h2>Protect the person, keep the medicine</h2>
          </div>
          <div className="features-grid">
            {features.map(({ icon: Icon, title, description }) => (
              <article className="feature-card raised" key={title}>
                <span className="feature-icon inset">
                  <Icon size={26} strokeWidth={2} />
                </span>
                <div>
                  <h3>{title}</h3>
                  <p>{description}</p>
                </div>
              </article>
            ))}
          </div>
        </section>
        <section id="trust" className="trust-section">
          <div className="eyebrow">Trust and privacy</div>
          <h2>Built to stay on your machine</h2>
          <div className="trust-grid">
            {trust.map((item) => (
              <div key={item.title}>
                <h3>{item.title}</h3>
                <p>{item.description}</p>
              </div>
            ))}
          </div>
          <div className="keeps-removes">
            <div>
              <h3>What it removes</h3>
              <p>
                Names in Latin and Devanagari
                <br />
                Aadhaar-like, ABHA, UHID and phone numbers
                <br />
                Fine addresses, PIN codes and landmarks
                <br />
                Exact birth dates
              </p>
            </div>
            <div>
              <h3>What it keeps</h3>
              <p>
                Blood pressure, HbA1c and lab values
                <br />
                Doses, units and frequency
                <br />
                Diagnoses and procedure codes
                <br />
                Event order and length of stay
              </p>
            </div>
          </div>
        </section>
        <section id="dashboard" className="landing-section">
          <div className="section-heading">
            <div className="eyebrow">Dashboard</div>
            <h2>Inside the dashboard</h2>
          </div>
          <div className="dashboard-preview-grid">
            <Link to="/dashboard/intake" className="dashboard-preview raised">
              <div className="mini-preview inset">
                <div>
                  <span>registration.csv</span>
                  <span className="text-green">✓ Ready</span>
                </div>
                <div>
                  <span>consultation.json</span>
                  <span className="text-green">✓ Ready</span>
                </div>
                <div>
                  <span>scan_0042.pdf</span>
                  <span className="text-amber">! Quarantined</span>
                </div>
              </div>
              <h3>Batch intake</h3>
              <p>Add files, choose the policy, start the run.</p>
              <span className="preview-link">
                Explore intake <FileCheck2 size={16} />
              </span>
            </Link>
            <Link to="/dashboard/review" className="dashboard-preview raised">
              <div className="mini-preview inset">
                <div>
                  <span>Mohammad Arif</span>
                  <span className="text-green">✓ NS-07</span>
                </div>
                <div>
                  <span lang="hi">मोहम्मद आरिफ</span>
                  <span className="text-green">✓ NS-07</span>
                </div>
                <div>
                  <span>Arif</span>
                  <span className="text-amber">! Held</span>
                </div>
              </div>
              <h3>Link review</h3>
              <p>See why records linked, and why one did not.</p>
              <span className="preview-link">
                Explore review <Link2 size={16} />
              </span>
            </Link>
            <Link to="/dashboard/release" className="dashboard-preview raised">
              <div className="mini-preview release-mini inset">
                <span className="blocked-label">× Export blocked</span>
                <small>References ✓ · Clinical values ✓ · Schema ✓</small>
              </div>
              <h3>Release checks</h3>
              <p>A clear verdict beside the clinical values kept.</p>
              <span className="preview-link">
                Explore checks <ShieldCheck size={16} />
              </span>
            </Link>
          </div>
        </section>
        <section className="landing-cta">
          <div className="saffron-dots" aria-hidden="true" />
          <h2>See it on a synthetic batch</h2>
          <Link to="/dashboard/intake" className="button button-primary button-large">
            Launch dashboard
          </Link>
          <p>
            NijSetu · Project RAKSHA · Synthetic competition data only. Illustrative names and
            values.
          </p>
        </section>
      </main>
      <footer className="landing-footer">
        <Brand compact />
        <span>Many records. One patient. Safely shared.</span>
        <a href="#trust">
          <LockKeyhole size={14} /> Local by design
        </a>
      </footer>
    </div>
  );
}
