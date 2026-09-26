import { Suspense } from "react";

import { Panel, PanelWait } from "../panel";
import { DEFAULT_SPAN, spanFrom } from "../span";
import {
  refreshDelivered, refreshDistricts, refreshEveryone, refreshPlans,
  refreshPrices, refreshTiles,
} from "./actions";
import {
  AlertDistricts, AlertPrices, AlertsDelivered, EveryoneTable, PlanMix,
  SubscriberTiles,
} from "./bodies";

export const dynamic = "force-dynamic";

export default async function SubscribersPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const span = spanFrom(params.w ?? DEFAULT_SPAN).key;
  const page = Math.max(1, Number(params.p ?? 1) || 1);

  return (
    <>
      <div className="dash-head">
        <h1>Subscribers</h1>
      </div>

      <Panel wide refresh={refreshTiles.bind(null, span, page)}>
        <Suspense fallback={<PanelWait />}>
          <SubscriberTiles span={span} page={page} />
        </Suspense>
      </Panel>

      <div className="dash-row dash-row-wide">
        <Panel
          title="Alerts delivered"
          why="Сколько уведомлений ушло за период, по времени. Всплески вечером — нормально: объявления публикуют неравномерно."
          refresh={refreshDelivered.bind(null, span)}
        >
          <Suspense fallback={<PanelWait />}>
            <AlertsDelivered span={span} />
          </Suspense>
        </Panel>

        <Panel
          title="Where the alerts went"
          why="Районы, по которым чаще всего совпадают фильтры подписчиков."
          refresh={refreshDistricts.bind(null, span)}
        >
          <Suspense fallback={<PanelWait />}>
            <AlertDistricts span={span} />
          </Suspense>
        </Panel>
      </div>

      <div className="dash-row dash-row-wide">
        <Panel
          title="By rent"
          why="Разбивка отправленных объявлений по арендной плате, полосами по £250. Показывает, в каком бюджете люди действительно ищут."
          refresh={refreshPrices.bind(null, span)}
        >
          <Suspense fallback={<PanelWait />}>
            <AlertPrices span={span} />
          </Suspense>
        </Panel>

        <Panel
          title="Plans"
          why="Сколько аккаунтов на каком тарифе. trial — пробный период, free — то, куда падает закончившийся план, week и month — платные."
          refresh={refreshPlans}
        >
          <Suspense fallback={<PanelWait />}>
            <PlanMix />
          </Suspense>
        </Panel>
      </div>

      <Panel
        wide
        title="Everyone"
        why="По 20 на страницу, новые сверху. Нажмите на номер — вся аналитика по человеку, смена плана, его платежи и история фильтров. «Free window» — сколько осталось от 24 часов, в которые WhatsApp разрешает писать свободным текстом с фотографией и не берёт денег. Окно открывает только входящее сообщение от человека; нажатие кнопки-ссылки его не продлевает."
        refresh={refreshEveryone.bind(null, span, page)}
      >
        <Suspense fallback={<PanelWait />}>
          <EveryoneTable span={span} page={page} />
        </Suspense>
      </Panel>
    </>
  );
}
