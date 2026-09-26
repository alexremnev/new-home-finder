import { Suspense } from "react";

import { Panel, PanelWait } from "../panel";
import { DEFAULT_SPAN, spanFrom } from "../span";
import { refreshChart, refreshFacet, refreshTiles } from "./actions";
import { FACETS, VisitorChart, VisitorFacet, VisitorTiles } from "./bodies";

export const dynamic = "force-dynamic";

// Every card fetches on its own, inside its own Suspense boundary, so the
// queries run in parallel and the page arrives without waiting for the
// slowest of twelve. Each then refreshes on its own too — see Panel.
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

      <Panel wide refresh={refreshTiles.bind(null, span)}>
        <Suspense fallback={<PanelWait />}>
          <VisitorTiles span={span} />
        </Suspense>
      </Panel>

      <Panel
        wide
        title="Visitors over time"
        refresh={refreshChart.bind(null, span)}
        why="Шаг бакета зависит от выбранного сверху периода и подписан под заголовком. Считается по first_at, то есть по времени прихода, поэтому один посетитель попадает ровно в один бакет. Разбивки ниже считаются по дням: строка в site_visits — одна на человека в день, поэтому часовой период читает сегодняшний день целиком."
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
            refresh={refreshFacet.bind(null, span, facet.key)}
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
