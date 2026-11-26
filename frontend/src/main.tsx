import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { useEffect } from 'react';
import { WorkspaceProvider } from './Workspace';
import DashboardLayout from './components/DashboardLayout';
import Landing from './pages/Landing';
import Intake from './pages/Intake';
import Review from './pages/Review';
import Release from './pages/Release';
import Policy from './pages/Policy';
import History from './pages/History';
import { Brand } from './components/UI';
import './styles.css';

function ScrollManager() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    if (hash) {
      requestAnimationFrame(() => document.getElementById(hash.slice(1))?.scrollIntoView());
    } else window.scrollTo(0, 0);
    const section = pathname.split('/').pop();
    const titles: Record<string, string> = {
      intake: 'Batch intake',
      review: 'Link review',
      release: 'Release checks',
      policy: 'Privacy policy',
      history: 'Run history',
    };
    document.title = titles[section || '']
      ? `${titles[section || '']} · NijSetu`
      : 'NijSetu · Healthcare privacy firewall';
  }, [pathname, hash]);
  return null;
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <WorkspaceProvider>
        <a className="skip-link" href="#main-content">
          Skip to content
        </a>
        <ScrollManager />
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/dashboard" element={<DashboardLayout />}>
            <Route index element={<Navigate to="intake" replace />} />
            <Route path="intake" element={<Intake />} />
            <Route path="review" element={<Review />} />
            <Route path="release" element={<Release />} />
            <Route path="policy" element={<Policy />} />
            <Route path="history" element={<History />} />
          </Route>
          <Route
            path="*"
            element={
              <main id="main-content" className="not-found">
                <Brand />
                <p className="eyebrow">A little off the path</p>
                <h1>Let’s get you back.</h1>
                <p>This page doesn’t exist, but your workspace is right here.</p>
                <Link to="/dashboard/intake" className="button button-primary">
                  Open workspace
                </Link>
                <Link to="/" className="text-link">
                  Back to home
                </Link>
              </main>
            }
          />
        </Routes>
      </WorkspaceProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
