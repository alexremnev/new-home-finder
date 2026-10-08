import { Suspense } from "react";

import { Panel, PanelWait } from "../panel";
import { DEFAULT_SPAN, spanFrom } from "../span";
import { FACETS, VisitorChart, VisitorFacet, VisitorTiles } from "./bodies";

export const dynamic = "force-dynamic";

// Every card fetches on its own, inside its own Suspense boundary, so the
// queries run in parallel and the page arrives without waiting for the
// slowest of twelve.
export default async function VisitorsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const span = spanFrom(params.w ?? DEFAULT_SPAN).key;

  return (
    <>
      <div className="dash-head">
        <h1>Visitors</h1>
      </div>

      <Panel wide>
        <Suspense fallback={<PanelWait />}>
          <VisitorTiles span={span} />
        </Suspense>
      </Panel>

      <Panel
        wide
        title="Visitors over time"
        why="Шаг бакета зависит от выбранного сверху периода и подписан под заголовком. Считается по first_at, то есть по времени прихода, поэтому один посетитель попадает ровно в один бакет. Плитки и разбивки читают ровно тот же период и то же время прихода, поэтому сумма столбиков сходится с плиткой «Visitors». Строка в site_visits — одна на человека в день, поэтому «уникальных за неделю» спросить нельзя: за период складываются уникальные по дням."
      >
        <Suspense fallback={<PanelWait />}>
          <VisitorChart span={span} />
        </Suspense>
      </Panel>

      {/* Everything a request tells us about where somebody came from, one card
          each. Robots are not in any of it: a visit is only recorded when the
          user agent does not name a robot and does name a browser we know. */}
      <div className="dash-row">
        {FACETS.map((facet) => (
          <Panel
            key={facet.key}
            title={facet.title}
            why={facet.why}
          >
            <Suspense fallback={<PanelWait />}>
              <VisitorFacet span={span} facet={facet.key} />
            </Suspense>
          </Panel>
        ))}
      </div>
    </>
  );
}
