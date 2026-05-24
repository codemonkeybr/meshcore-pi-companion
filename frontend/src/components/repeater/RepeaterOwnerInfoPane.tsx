import { RepeaterPane, NotFetched, KvRow } from './repeaterPaneShared';
import type { RepeaterOwnerInfoResponse, PaneState } from '../../types';

function LabeledBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="py-0.5">
      <span className="text-sm text-muted-foreground whitespace-nowrap">{label}</span>
      <p className="text-sm font-medium mt-0.5 break-words">{value}</p>
    </div>
  );
}

export function OwnerInfoPane({
  data,
  state,
  onRefresh,
  disabled,
}: {
  data: RepeaterOwnerInfoResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
}) {
  return (
    <RepeaterPane title="Owner Info" state={state} onRefresh={onRefresh} disabled={disabled}>
      {!data ? (
        <NotFetched />
      ) : (
        <div className="space-y-1">
          <LabeledBlock label="Owner Info" value={data.owner_info ?? '—'} />
          <KvRow label="Guest Password" value={data.guest_password ?? '—'} />
        </div>
      )}
    </RepeaterPane>
  );
}
