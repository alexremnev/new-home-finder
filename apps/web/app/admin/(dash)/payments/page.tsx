import { Suspense } from "react";

import { Panel, PanelWait } from "../panel";
import { DEFAULT_SPAN, spanFrom } from "../span";
import {
  ByPlan, ByProvider, FreeExtensions, LatestPayments, PaymentTiles, TakenPerDay,
} from "./bodies";

export const dynamic = "force-dynamic";

export default async function PaymentsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const span = spanFrom(params.w ?? DEFAULT_SPAN).key;

  return (
    <>
      <div className="dash-head">
        <h1>Payments</h1>
      </div>

      <Panel wide>
        <Suspense fallback={<PanelWait />}>
          <PaymentTiles span={span} />
        </Suspense>
      </Panel>

      <div className="dash-row dash-row-wide">
        <Panel title="Taken per day">
          <Suspense fallback={<PanelWait />}>
            <TakenPerDay span={span} />
          </Suspense>
        </Panel>

        <Panel
          title="By plan"
          why="Сколько денег принёс каждый тариф за период. Пусто — значит за это время не платили."
        >
          <Suspense fallback={<PanelWait />}>
            <ByPlan span={span} />
          </Suspense>
        </Panel>

        <Panel
          title="By provider"
          why="stripe — оплата картой; bank_transfer — выдано вручную через /grant; manual — правка из админки."
        >
          <Suspense fallback={<PanelWait />}>
            <ByProvider span={span} />
          </Suspense>
        </Panel>
      </div>

      <Panel
        wide
        title="Free extensions"
        why="Дни, выданные без оплаты из админки — аудит действий extend_plan. Это не выручка, но это доступ, поэтому отдельной таблицей. Журнал, а не срез: выбранный сверху период на него не влияет, а «Show more» догружает следующие десять."
      >
        <Suspense fallback={<PanelWait />}>
          <FreeExtensions />
        </Suspense>
      </Panel>

      <Panel
        wide
        title="Latest"
        why="Платежи, новые сверху, независимо от выбранного сверху периода: это журнал, а не срез. По двадцать пять за раз, дальше — «Show more»."
      >
        <Suspense fallback={<PanelWait />}>
          <LatestPayments />
        </Suspense>
      </Panel>
    </>
  );
}
