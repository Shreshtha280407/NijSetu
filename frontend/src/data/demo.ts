// Illustrative frontend data. Nothing here is an engine result.
export type PolicyName = 'balanced' | 'strict';
export type BatchFile = {
  id: string;
  name: string;
  size: number;
  format: string;
  supported: boolean;
};

export const sampleFiles: BatchFile[] = [
  { id: 'registration', name: 'registration.csv', size: 2430, format: 'CSV', supported: true },
  { id: 'consultation', name: 'consultation.json', size: 4820, format: 'JSON', supported: true },
  { id: 'discharge', name: 'discharge.txt', size: 1860, format: 'TXT', supported: true },
  { id: 'opd', name: 'opd_note.txt', size: 1040, format: 'TXT', supported: true },
  { id: 'scan', name: 'scan_0042.pdf', size: 128600, format: 'PDF', supported: false },
];

export const sampleRecords = [
  {
    id: 'registration',
    name: 'Mohammad Arif',
    file: 'registration.csv',
    type: 'Registration',
    evidence: 'Scoped UHID + corroborating name',
    tag: 'SCOPED_REF',
    linked: true,
  },
  {
    id: 'consultation',
    name: 'मोहम्मद आरिफ',
    file: 'consultation.json',
    type: 'Consultation',
    evidence: 'Cross-script alias + scoped reference',
    tag: 'SCRIPT_ALIAS',
    linked: true,
  },
  {
    id: 'discharge',
    name: 'Md. Arif',
    file: 'discharge.txt',
    type: 'Discharge summary',
    evidence: 'Normalized alias + scoped reference',
    tag: 'SCOPED_REF',
    linked: true,
  },
  {
    id: 'opd',
    name: 'Arif',
    file: 'opd_note.txt',
    type: 'OPD note',
    evidence: 'Name only. No corroborating identifier.',
    tag: 'NAME_ONLY',
    linked: false,
  },
];

export const policies = {
  balanced: {
    label: 'Balanced',
    description: 'Protect identity while preserving research utility.',
    age: '10-year bands',
    dates: '±30 days',
    geography: 'District retained',
    cohort: '3',
    providers: 'Retained',
    facilities: 'Retained',
  },
  strict: {
    label: 'Strict',
    description: 'Broader generalization for more sensitive releases.',
    age: '20-year bands',
    dates: '±45 days',
    geography: 'Suppressed',
    cohort: '5',
    providers: 'Suppressed',
    facilities: 'Suppressed',
  },
} as const;

export function formatSize(bytes: number) {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
}

export function downloadJson(name: string, value: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }),
  );
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
