import { useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";
import { useDesk } from "../../core/api";
import { EmptyState, ErrorState, Skeleton } from "../../shared/States";
import { EmailPane } from "./EmailPane";
import { Inbox } from "./Inbox";
import { LinesPane } from "./LinesPane";

export function DeskPage() {
  const desk = useDesk();
  const { rfq } = useSearch({ from: "/" });
  const navigate = useNavigate({ from: "/" });
  const [active, setActive] = useState<string>();
  if (desk.isPending) return <div className="grid grid-cols-[var(--width-inbox)_1fr]"><Skeleton label="Loading enquiries" lines={10} /><Skeleton label="Loading email" /></div>;
  if (desk.isError) return <ErrorState message={desk.error.message} onRetry={() => desk.refetch()} />;
  const rfqs = desk.data.rfqs;
  if (rfqs.length === 0) return <EmptyState title="The inbox is empty">New enquiries appear here as soon as the worker has read them.</EmptyState>;
  const current = rfqs.find((r) => r.message_id === rfq) ?? rfqs.find((r) => r.status === "review") ?? rfqs[0];
  return (
    <div className="grid h-[calc(100vh-3.5rem)] grid-cols-1 lg:grid-cols-[var(--width-inbox)_minmax(0,1fr)_var(--width-lines)]">
      <Inbox rfqs={rfqs} selected={current.message_id} onSelect={(id) => navigate({ search: { rfq: id } })} />
      <EmailPane rfq={current} active={active} onActive={setActive} />
      <LinesPane rfq={current} active={active} onActive={setActive} />
    </div>
  );
}
