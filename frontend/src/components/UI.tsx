import { Check, CircleHelp, ShieldCheck, X, type LucideIcon } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { ReactNode } from 'react';

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link to="/" className={`brand ${compact ? 'brand-compact' : ''}`} aria-label="NijSetu home">
      <img className="brand-mark" src="/brand/nijsetu-logo.svg" alt="" width="46" height="28" />
      <span>NijSetu</span>
    </Link>
  );
}

export function Badge({
  children,
  tone = 'neutral',
  dot = false,
}: {
  children: ReactNode;
  tone?: 'neutral' | 'success' | 'warning' | 'indigo' | 'danger';
  dot?: boolean;
}) {
  return (
    <span className={`badge badge-${tone}`}>
      {dot && <span className="status-dot" />}
      {children}
    </span>
  );
}

export function PageHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action && <div className="heading-action">{action}</div>}
    </div>
  );
}

export function Stat({
  icon: Icon,
  label,
  value,
  detail,
  accent = false,
}: {
  icon: LucideIcon;
  label: string;
  value: string | number;
  detail: string;
  accent?: boolean;
}) {
  return (
    <div className={`stat-card raised ${accent ? 'stat-accent' : ''}`}>
      <div className="stat-top">
        <span>{label}</span>
        <Icon size={19} />
      </div>
      <div className="stat-value">{value}</div>
      <div className="stat-detail">{detail}</div>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state raised">
      <span className="icon-well">
        <ShieldCheck size={30} />
      </span>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function InfoNote({ children }: { children: ReactNode }) {
  return (
    <div className="info-note">
      <CircleHelp size={18} />
      <span>{children}</span>
    </div>
  );
}

export function CheckItem({ children, passed = true }: { children: ReactNode; passed?: boolean }) {
  return (
    <span className="check-item">
      <span className={`check-icon ${passed ? '' : 'check-negative'}`}>
        {passed ? <Check size={14} /> : <X size={14} />}
      </span>
      {children}
    </span>
  );
}
