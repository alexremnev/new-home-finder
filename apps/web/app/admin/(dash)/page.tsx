import { Suspense } from "react";

import {
  Duplicates, Faults, Feeds, Health, Jobs, LastRuns, ListingsCreated, LogLines,
  MessagesRead, QueueTiles, RunsChart,
} from "./bodies";
import { Panel, PanelWait } from "./panel";
import { DEFAULT_SPAN, spanFrom } from "./span";
import {
  refreshDuplicates, refreshFaults, refreshFeeds, refreshHealth, refreshJobs,
  refreshLastRuns, refreshListings, refreshLog, refreshMessages, refreshQueue,
  refreshRuns,
} from "./actions";

export const dynamic = "force-dynamic";

export default async function SystemPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const span = spanFrom(params.w ?? DEFAULT_SPAN).key;
  const page = Math.max(1, Number(params.p ?? 1) || 1);
  const job = params.job || undefined;
  const level = params.level || undefined;

  return (
    <>
      <Panel wide refresh={refreshHealth.bind(null, span)}>
        <Suspense fallback={<PanelWait />}>
          <Health span={span} />
        </Suspense>
      </Panel>

      <Panel
        wide
        foldable
        title="Problems"
        why="Проверки, которые смотрят дальше кода возврата задачи: встала ли очередь, есть ли куда отправлять, не сменил ли источник формат. Задача может завершиться успешно и при этом ничего не сделать. Периоды у проверок свои, не зависящие от выбранного сверху: ошибки задач — за 48 часов, неразобранные сообщения — за 7 дней, «ничего не прочитано» — порог 6 часов, «доставка встала» — очередь старше часа. Дата в строке — последний раз, когда это случилось."
        refresh={refreshFaults}
      >
        <Suspense fallback={<PanelWait />}>
          <Faults />
        </Suspense>
      </Panel>

      <div className="dash-head">
        <h1>System</h1>
      </div>

      <Panel wide refresh={refreshFeeds.bind(null, span)}>
        <Suspense fallback={<PanelWait />}>
          <Feeds span={span} />
        </Suspense>
      </Panel>

      <Panel
        wide
        title="Duplicates across portals"
        why="Копией считается объявление с тем же полным индексом, той же ценой и тем же числом спален и ванных, что у более раннего объявления, впервые увиденного в тот же лондонский день, — и обязательно с другого портала. Одна и та же квартира приходит и с Rightmove, и с Zoopla: отправляется та, что пришла первой, вторая сохраняется ради своей ссылки, но помечается и не уходит никому. Два объявления с одного портала копиями не считаются — это, как правило, дом-новостройка, где сорок одинаковых квартир действительно сдаются отдельно. Объявления без полного индекса не сравниваются вовсе. Эти копии исключены и из счёта по районам, и из общего числа новых объявлений за день."
        refresh={refreshDuplicates.bind(null, span)}
      >
        <Suspense fallback={<PanelWait />}>
          <Duplicates span={span} />
        </Suspense>
      </Panel>

      <Panel wide refresh={refreshJobs.bind(null, span)}>
        <Suspense fallback={<PanelWait />}>
          <Jobs span={span} />
        </Suspense>
      </Panel>

      <Panel
        wide
        title="Runs"
        why="Каждый столбик — окно времени. Зелёное: прогоны завершились успешно. Красное: упали или прошли с ошибками. Так видно разницу между «сломалось один раз» и «сломано весь день»."
        refresh={refreshRuns.bind(null, span)}
      >
        <Suspense fallback={<PanelWait />}>
          <RunsChart span={span} />
        </Suspense>
      </Panel>

      <div className="dash-row dash-row-wide">
        <Panel
          title="Messages read"
          why="Сообщения, прочитанные из фида. Ровно это делает ingest: если линия на нуле, а задача зелёная — значит в канале тихо, а не сломано."
          refresh={refreshMessages.bind(null, span)}
        >
          <Suspense fallback={<PanelWait />}>
            <MessagesRead span={span} />
          </Suspense>
        </Panel>

        <Panel
          title="Listings created"
          why="Сколько из прочитанных сообщений стало объявлениями. Расхождение с предыдущим графиком — это дубли и то, что парсер не понял."
          refresh={refreshListings.bind(null, span)}
        >
          <Suspense fallback={<PanelWait />}>
            <ListingsCreated span={span} />
          </Suspense>
        </Panel>
      </div>

      <Panel wide refresh={refreshQueue.bind(null, span)}>
        <Suspense fallback={<PanelWait />}>
          <QueueTiles span={span} />
        </Suspense>
      </Panel>

      <Panel
        wide
        title="Last runs"
        why="Отдельные прогоны, свежие сверху, последние двенадцать независимо от выбранного периода. Trigger показывает, кто запустил: schedule — таймер, manual — вы руками. skipped_locked значит, что предыдущий прогон ещё шёл, и это норма, а не сбой."
        refresh={refreshLastRuns}
      >
        <Suspense fallback={<PanelWait />}>
          <LastRuns />
        </Suspense>
      </Panel>

      <Panel
        wide
        title="Log"
        why="Строки, которые задачи пишут о себе. error — что-то сломалось, warn — сработала защита, info — обычный ход дела. По 10 на страницу."
        refresh={refreshLog.bind(null, span, job, level, page)}
      >
        <Suspense fallback={<PanelWait />}>
          <LogLines span={span} job={job} level={level} page={page} />
        </Suspense>
      </Panel>
    </>
  );
}
