import { Activity, Download, FlaskConical, History as HistoryIcon } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Badge, EmptyState, InfoNote, PageHeading } from '../components/UI';
import { downloadJson } from '../data/demo';
import { useWorkspace } from '../Workspace';

export default function History() {
  const { history, policies } = useWorkspace();
  return (
    <>
      <PageHeading
        eyebrow="Every step, accounted for"
        title="A record of careful work."
        description="Your engine runs, their policies, and the decisions made along the way."
        action={
          <Link to="/dashboard/intake" className="button button-small">
            <FlaskConical size={17} /> Open batch intake
          </Link>
        }
      />
      {!history.length ? (
        <EmptyState
          title="Your first run is a good place to start."
          description="Run a demo or uploaded dataset to create your first activity record. Completed runs will appear here for this session."
          action={
            <Link to="/dashboard/intake" className="button button-primary">
              <Activity size={17} /> Start a run
            </Link>
          }
        />
      ) : (
        <section className="raised history-panel">
          <div className="panel-heading">
            <div>
              <h2>Engine run history</h2>
              <p>
                {history.length} {history.length === 1 ? 'run' : 'runs'} in this browser session
              </p>
            </div>
            <HistoryIcon size={23} />
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Run</th>
                  <th scope="col">Started</th>
                  <th scope="col">Policy</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Summary</th>
                </tr>
              </thead>
              <tbody>
                {history.map((run) => (
                  <tr key={run.id}>
                    <th scope="row">
                      <span className="run-id">{run.id}</span>
                      <span className="table-secondary">
                        {run.demo ? 'Demo dataset' : 'Uploaded dataset'} · {run.file_count} files
                      </span>
                    </th>
                    <td>
                      {new Date(run.date).toLocaleDateString(undefined, {
                        day: 'numeric',
                        month: 'short',
                      })}
                      <span className="table-secondary">
                        {new Date(run.date).toLocaleTimeString(undefined, {
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                    </td>
                    <td>{policies[run.policy].label}</td>
                    <td>
                      <Badge
                        tone={run.result?.release_ready && run.reviewed ? 'success' : 'warning'}
                      >
                        {!run.result?.release_ready
                          ? 'Publication blocked'
                          : run.reviewed
                            ? 'Review complete'
                            : 'Review pending'}
                      </Badge>
                    </td>
                    <td>
                      <button
                        className="icon-button"
                        aria-label={`Download summary for ${run.id}`}
                        onClick={() =>
                          downloadJson(`nijsetu-${run.id.toLowerCase()}.json`, {
                            ...run,
                            result: undefined,
                            report: run.result?.report,
                            analysis: run.result?.analysis,
                            checks: run.result?.checks,
                          })
                        }
                      >
                        <Download size={18} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <InfoNote>
        History lives in memory for this session. Refreshing clears the workspace; patient data and
        filenames from uploaded files are never saved to browser storage.
      </InfoNote>
    </>
  );
}
