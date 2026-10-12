import { Suspense } from "react";

import { Panel, PanelWait } from "../panel";
import { DEFAULT_SPAN, spanFrom } from "../span";
import { ProxyTasks } from "./bodies";

export const dynamic = "force-dynamic";

// One widget per scraper job, each saying how much of its traffic went through
// the residential proxy over the range chosen in the bar.
export default async function ProxyPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const span = spanFrom(params.w ?? DEFAULT_SPAN).key;

  return (
    <>
      <div className="dash-head">
        <h1>Proxy</h1>
      </div>

      <Panel
        wide
        title="Proxy spend by scraper"
        why="Сколько каждая задача-скрапер отправила через резидентский прокси за выбранный сверху период. «proxied» — число запросов, ушедших через тоннель, «MB paid» — байты, которые за это выставит DataImpulse; рядом «requests» и «MB total» — весь трафик задачи, проксированный и прямой, чтобы было видно не только то, что прокси понёс, но и то, что он не понёс. Цифры берутся из счётчиков самого прогона (counters в job_stages), то есть из счётчика передачи libcurl, а не из размера распакованных страниц, — это ровно то, что провайдер считает тоннелем. Группировка по имени задачи, а не по источнику: zoopla и zoopla_london читают один сайт под одним source_key, но это два расписания с разной стоимостью, и в сумме по источнику их счёт не разделить. Полоса слева — это оценка, а не факт: прокси нужен по существу только zoopla_london (Zoopla отдаёт 403 на адрес сервера), поэтому ноль у остальных — норма, а ноль у zoopla_london при идущих прогонах — поломка. SpareRoom закреплён на прямом маршруте в коде, и «pinned» означает именно это: сайт отказал, а платить за него сознательно не стали. «fallback» — запросы ушли ко второму провайдеру, что случается только после того, как основной пул потратил весь бюджет и страницу так и не отдал; это второй счёт и повод спросить у провайдера про качество пула. «rotations» — сколько выходных адресов прогон забраковал: раз в неделю это один плохой адрес в пуле, каждый прогон — пул, который портится."
      >
        <Suspense fallback={<PanelWait />}>
          <ProxyTasks span={span} />
        </Suspense>
      </Panel>
    </>
  );
}
