import { StepsDrawer } from '../../../components/StepsDrawer';

export default function DevStepsPage() {
  return (
    <main className="min-h-screen px-6 py-8">
      <div className="max-w-[640px]">
        <div className="text-[10px] font-bold tracking-[0.1em] uppercase">
          DEV UI CHECK
        </div>
        <h1 className="text-[18px] font-extrabold leading-[1.1]">
          TOOL CALL STEPS
        </h1>
        <p className="text-[12px] text-muted mt-2">
          Open the drawer below. It should show three mock tool calls and expand
          to reveal args plus response summaries.
        </p>
        <StepsDrawer scanId="dev-mock" />
      </div>
    </main>
  );
}
