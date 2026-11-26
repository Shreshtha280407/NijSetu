import { useState } from 'react';
import {
  Activity,
  BookOpen,
  Check,
  ChevronRight,
  ClipboardCheck,
  FileInput,
  Home,
  Link2,
  LockKeyhole,
  Menu,
  Settings2,
  ShieldCheck,
  X,
} from 'lucide-react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { Brand, Badge } from './UI';
import { useWorkspace } from '../Workspace';

const links = [
  { to: '/dashboard/intake', label: 'Batch intake', icon: FileInput, step: '01' },
  { to: '/dashboard/review', label: 'Link review', icon: Link2, step: '02' },
  { to: '/dashboard/release', label: 'Release checks', icon: ClipboardCheck, step: '03' },
];

export default function DashboardLayout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const { pathname } = useLocation();
  const { notice, setNotice, analyzed, reviewed, running, sample, connected } = useWorkspace();
  const title = [
    ...links,
    { to: '/dashboard/policy', label: 'Privacy policy' },
    { to: '/dashboard/history', label: 'Run history' },
  ].find((item) => item.to === pathname)?.label;

  return (
    <div className="dashboard-shell">
      {menuOpen && (
        <button
          className="sidebar-backdrop"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`}>
        <div className="sidebar-brand">
          <Brand />
          <button
            className="icon-button mobile-only"
            onClick={() => setMenuOpen(false)}
            aria-label="Close navigation"
          >
            <X size={20} />
          </button>
        </div>
        <div className="workspace-label">
          <span className="workspace-avatar">
            <ShieldCheck size={21} />
          </span>
          <div>
            <strong>Local workspace</strong>
            <span>Project RAKSHA</span>
          </div>
          <LockKeyhole size={14} />
        </div>
        <div className="nav-group-label">YOUR WORKFLOW</div>
        <nav aria-label="Dashboard navigation">
          {links.map(({ to, label, icon: Icon, step }) => (
            <NavLink
              key={to}
              to={to}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`}
            >
              <Icon size={20} />
              <span>{label}</span>
              <span className="nav-step" aria-hidden="true">
                {step}
              </span>
            </NavLink>
          ))}
          <div className="nav-group-label second-group">WORKSPACE</div>
          <NavLink
            to="/dashboard/policy"
            onClick={() => setMenuOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`}
          >
            <Settings2 size={20} />
            <span>Privacy policy</span>
          </NavLink>
          <NavLink
            to="/dashboard/history"
            onClick={() => setMenuOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`}
          >
            <Activity size={20} />
            <span>Run history</span>
          </NavLink>
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-note inset">
            <LockKeyhole size={21} />
            <strong>Your data stays here.</strong>
            <p>Processing stays in the local Python engine.</p>
            <Link to="/#trust">
              About trust & privacy <BookOpen size={14} />
            </Link>
          </div>
          <Link to="/" className="back-home">
            <Home size={17} /> Back to home
          </Link>
          <div className="sidebar-foot">
            NijSetu <span>v0.2 · Engine workspace</span>
          </div>
        </div>
      </aside>
      <div className="dashboard-body">
        <header className="workspace-header">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-only"
              onClick={() => setMenuOpen(true)}
              aria-label="Open navigation"
            >
              <Menu size={22} />
            </button>
            <span>Workspace</span>
            <ChevronRight size={14} />
            <strong>{title}</strong>
          </div>
          <div className="header-right">
            <span className="header-private">
              <LockKeyhole size={14} /> On this device
            </span>
            <Badge tone="success" dot>
              {connected ? 'Engine connected' : 'Engine offline'}
            </Badge>
          </div>
        </header>
        <main id="main-content" className="workspace-main">
          <div className="demo-banner">
            <ShieldCheck size={18} />
            <p>
              <strong>{sample ? 'Demo dataset' : 'Uploaded dataset'}</strong>
              <span>
                Run detection, patient linkage, sanitization and validation with the Python engine.
              </span>
            </p>
            <Badge tone="indigo">{sample ? 'Demo data' : 'Your data'}</Badge>
          </div>
          <div className="workflow-progress" aria-label="Workflow progress">
            {links.map((item, index) => {
              const complete = index === 0 ? analyzed : index === 1 ? reviewed : false;
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className={`workflow-step ${pathname === item.to ? 'current' : ''} ${complete ? 'complete' : ''}`}
                >
                  <span className="workflow-number">
                    {complete ? <Check size={14} /> : index + 1}
                  </span>
                  {item.label}
                  {index === 0 && running && <span className="running-label">Running</span>}
                </Link>
              );
            })}
          </div>
          <Outlet />
          <footer className="workspace-footer">
            <span>
              <ShieldCheck size={14} /> Designed for privacy. Built for clarity.
            </span>
            <span>Project RAKSHA · Local processing</span>
          </footer>
        </main>
      </div>
      {notice && (
        <div className="toast" role="status">
          <Check size={18} />
          <span>{notice}</span>
          <button
            className="icon-button"
            onClick={() => setNotice('')}
            aria-label="Dismiss notification"
          >
            <X size={17} />
          </button>
        </div>
      )}
    </div>
  );
}
