import { Suspense } from "react";

import { Panel, PanelWait } from "../panel";
import { DEFAULT_SPAN, spanFrom } from "../span";
import { BrowserCost, HostTiles } from "./bodies";

export const dynamic = "force-dynamic";

// The machine the worker runs on, and what the headless browser costs it.
export default async function HostPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const span = spanFrom(params.w ?? DEFAULT_SPAN).key;

  return (
    <>
      <div className="dash-head">
        <h1>Host</h1>
      </div>

      <Panel
        wide
        title="The machine"
        why="Процессоры, память и диск того самого сервера, на котором крутится воркер — 2 vCPU, 4 ГБ, 40 ГБ, и на нём больше ничего нет: Postgres живёт в Supabase, веб на Vercel. Цифры берутся не от агента мониторинга, а из журнала прогонов: каждый прогон при завершении дописывает один снимок бокса в свои counters, и «сейчас» здесь означает «снимок самого свежего прогона». Так сделано потому, что node exporter на этой машине сам потребовал бы больше памяти, чем измеряемое, а firewall на ней сознательно не пускает внутрь ничего. Цена честная и её стоит назвать: разрешение — один снимок на прогон, поэтому всплеск между двумя прогонами сюда не попадает. Днём читатели ходят каждые 6–12 минут, этого хватает на вопрос «хватало ли ему памяти сегодня днём» и не хватает ни на какой профайлинг. Поле, которое не удалось прочитать, отсутствует, а не равно нулю: прогон со старого кода или хост без /proc не должен считаться снимком, показавшим ноль свободной памяти."
      >
        <Suspense fallback={<PanelWait />}>
          <HostTiles span={span} />
        </Suspense>
      </Panel>

      <Panel
        wide
        title="Headless browser"
        why="Что стоит безголовый Chromium и покупает ли он что-нибудь. Браузер здесь не транспорт: страницы по-прежнему тянет curl_cffi, а браузер раз в рабочие будни поднимается только затем, чтобы пройти челлендж Cloudflare и выписать cf_clearance в тот же куки-джар, по которому дальше едет curl. Поэтому запусков десятки в день, а не тысячи, и поэтому обе цифры читаются вместе: пиковая память против потолка слайса (MemoryMax=2G) и доля запусков, реально унёсших токен. Запуск, который сжёг полгигабайта и ушёл ни с чем, хуже незапуска — он стоил ресурсов и оставил zoopla_london под челленджем, а в сводном «бокс в порядке» этого не видно. Таймер покрывает только будни 08:00–18:00, когда zoopla_london ходит каждые шесть минут: это единственная полоса, где обещание «задержка не больше 30 минут» вообще действует, а ночью при часовом расписании его нарушает само расписание, а не Cloudflare."
      >
        <Suspense fallback={<PanelWait />}>
          <BrowserCost span={span} />
        </Suspense>
      </Panel>
    </>
  );
}
