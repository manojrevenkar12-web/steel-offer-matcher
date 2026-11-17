import { useDesk } from "../../core/api";
import { StatusMark } from "../../shared/StatusMark";
import { EmptyState, ErrorState, Skeleton } from "../../shared/States";

export function ToolsPage() {
  const desk = useDesk();
  if (desk.isPending) return <Skeleton label="Loading tools" />;
  if (desk.isError) return <ErrorState message={desk.error.message} onRetry={() => desk.refetch()} />;
  const { mcp, lists } = desk.data;
  return (
    <div className="mx-auto grid max-w-6xl gap-10 p-6 lg:grid-cols-2">
      <section>
        <h1 className="font-display text-2xl leading-tight">MCP server</h1>
        <p className="mt-2 text-steel">One surface for the desk's agents and outside clients. Every call is checked against the client's scopes and logged.</p>
        <h2 className="mt-5 text-sm font-medium text-steel">Tools</h2>
        <ul className="mt-2 flex flex-wrap gap-2">
          {mcp.tools.map((t) => <li key={t} className="rounded bg-panel px-2 py-1 text-sm ring-1 ring-rule">{t}</li>)}
        </ul>
        <h2 className="mt-5 text-sm font-medium text-steel">Clients and scopes</h2>
        <ul className="mt-2 space-y-2">
          {mcp.clients.map((c) => (
            <li key={c.client_id} className="rounded-[var(--radius-panel)] border border-rule bg-panel p-3">
              <div className="font-medium">{c.client_id}</div>
              <div className="mt-1 text-sm text-steel">{c.scopes.join(", ")}</div>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h2 className="font-display text-2xl leading-tight">Supplier stock lists</h2>
        <p className="mt-2 text-steel">The model maps a list's columns once per layout. Code checks the mapping on the values; a list that fails is held whole.</p>
        {lists.length === 0 ? <EmptyState title="No lists received" /> : (
          <ul className="mt-4 divide-y divide-rule rounded-[var(--radius-panel)] border border-rule bg-panel">
            {lists.map((l) => (
              <li key={l.filename} className="flex items-center justify-between gap-3 p-3 text-sm">
                <span className="truncate">{l.filename}</span>
                <span className="flex shrink-0 items-center gap-3 text-steel">
                  {l.items} lots {l.used_model ? "· mapped by the model" : "· known layout"}
                  <StatusMark value={l.status === "loaded" ? "ready" : "review"} />
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
