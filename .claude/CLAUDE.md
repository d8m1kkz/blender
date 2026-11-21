# CLAUDE.md

Этот файл — руководство для Claude Code по работе с репозиторием.

## Суть проекта

Учебный проект (без production-требований): веб-приложение, в котором пользователь задаёт параметры `x` и `y`, а сервер процедурно генерирует 3D-модель в Blender (Geometry Nodes) и отдаёт её как `.glb`. Браузер показывает модель через Three.js.

Поток данных:

```
Браузеры (Vue + Three.js, ModelViewer.vue) — сколько угодно, у всех одна общая корзина
   │  WebSocket /ws   (nginx проксирует в gen:8080/ws, минуя PHP)
   │  → {"type":"params",x,y,z,seq}      ← {"type":"ack",version,seq}
   │                                     ← {"type":"model",version,x,y,z} + бинарный кадр GLB
   ▼
gen (FastAPI, gen/web.py): state (желаемые x/y/z + version) → generation_loop
   ──unix-сокет /dev/shm/blender.sock──►  Blender-воркер (gen/worker.py) → /dev/shm/m_<uuid>.glb
   ◄── байты GLB читаются в память (ready), файл сразу удаляется; рассылка всем, если версия актуальна

MCP-клиенты (Claude Code и т.п.) ──POST /mcp (nginx → mcp:8000)──► mcp (mcp/server.py)
   └── тот же протокол: ws://gen:8080/ws?glb=0 — правки видят все веб-клиенты
```

Laravel (`web/`) сейчас лишь отдаёт страницу `welcome.blade.php` с Vue-компонентом `<model-viewer>`; бэкенд-логики PHP почти нет.

Модель в сцене — корзина (`Basket`); пользовательские `x`/`y` задают её размеры.

## Структура репозитория

| Путь | Назначение |
|---|---|
| `web/` | Laravel 13 (PHP ^8.3, образ на PHP 8.5-fpm), Vite 8, Vue 3, Tailwind 4, Three.js |
| `web/resources/js/app.js` | Точка входа: регистрирует компонент `model-viewer`, монтирует `#app` |
| `web/resources/js/components/ModelViewer.vue` | Основной UI: слайдеры x, y, z, WebSocket к gen (общая корзина), журнал полученных моделей, рендер GLB через `GLTFLoader.parseAsync` |
| `web/resources/views/welcome.blade.php` | Рабочая страница с `<model-viewer>`; `welcome1.blade.php` — запасной/старый вариант |
| `gen/` | Python-сервис генерации: `web.py` (FastAPI), `worker.py` (долгоживущий Blender), `basket.blend` (сцена), `.dockerignore` |
| `mcp/` | MCP-сервер корзины: `server.py` (SDK `mcp==2.3.0`, `MCPServer`, Streamable HTTP :8000/mcp, stateless), `Dockerfile` (Python 3.13) |
| `.mcp.json` | Подключение MCP-сервера `basket` к Claude Code в этом проекте: `http://localhost/mcp` |
| `web/nginx.conf` | Конфиг nginx: `location = /ws` → `gen:8080/ws` (WebSocket, заголовки `Upgrade`/`Connection`), `location = /mcp` → `mcp:8000/mcp` (`proxy_buffering off`), остальное → php-fpm (`php:9000`), root `public/`. В dev монтируется томом, в prod копируется в образ `web` (`COPY nginx.conf`) |
| `compose.yml` | Dev-окружение: сервисы `node`, `nginx`, `php`, `gen`, `mcp` (код монтируется томами; у `gen` команда `uvicorn … --reload`; у `mcp` перезагрузки нет — `docker compose restart mcp`) |
| `compose.prod.yml` | Prod-окружение: код внутри образов, без `node` и bind-mount'ов, `restart: unless-stopped`, лимит логов Docker, том `app-storage`; сервисы `nginx`, `php`, `gen`, `mcp` |
| `web/Dockerfile` | Multi-stage: `base` → `vendor` → `assets` (Vite build) → `app` (php-fpm) и `web` (nginx со статикой) |
| `gen/Dockerfile` | Один stage (без `prod`/`dev`): Blender (урезан: без `pip`, `setuptools`, `Cython`, `pxr`, `__pycache__`) + FastAPI и `uvicorn[standard]` (без него нет WebSocket), `COPY web.py worker.py basket.blend`, `CMD uvicorn` без `--reload`. Prod использует образ как есть; dev (`compose.yml`) монтирует `./gen:/app` поверх и переопределяет `command` с `--reload` |
| `deploy.sh` | Деплой на VPS: `./deploy.sh <IP>`; локально `docker compose build --push` (с `COMPOSE_FILE=compose.prod.yml`), затем по SSH: доставка compose-файла (как `compose.yml`) и `web/.env.production`, `cloud-init status --wait`, установка Docker, по каждому сервису (`gen`, `mcp`, `php`, `nginx`; nginx последним — ему нужны апстримы): `pull`, `up -d --no-build`, `image prune -f` |

## Запуск и команды

Всё работает в Docker Compose из корня:

```bash
docker compose up --build                                  # dev (compose.yml): node (Vite :5173), nginx (:80), php, gen (:8080)
docker compose -f compose.prod.yml build                   # prod
docker compose -f compose.prod.yml up -d
```

- Сайт: http://localhost (nginx), Vite HMR: :5173, генератор: WebSocket `/ws` (через nginx); текущая модель для отладки — http://localhost:8080/model (заголовок `X-Version`, только dev)
- **Перед сборкой `gen`** в `gen/` должен лежать `blender-linux-x64.tar.xz` (Dockerfile подключает его через `--mount=type=bind`). Архив в `.gitignore` (`*.tar.xz`), в git его нет.
- Первоначальная настройка Laravel (из `web/`, внутри php-контейнера): `composer setup` (install, `.env`, `key:generate`, `migrate`, `npm install`, `npm run build`).
- Фронтенд: `npm run dev` / `npm run build` (из `web/`). Node-сервис в compose запускает `npm run dev` сам.
- Тесты PHP: `php artisan test` (PHPUnit, `web/tests/`); сейчас только примерные тесты.
- MCP: http://localhost/mcp (Streamable HTTP); в Claude Code подключается из `.mcp.json`. Проверка без клиента: `curl -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' http://localhost/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`.
- Образы: `d8m1kkz/blender-php`, `d8m1kkz/blender-gen`, `d8m1kkz/blender-mcp`, `d8m1kkz/blender-nginx` (последний только в prod). Dev использует `target: base` для php; у `gen` stage'ей нет — dev и prod различаются только compose-файлом (volume + `command` в dev, код в образе в prod).
- Prod: секреты (`APP_KEY` и т.д.) — в `web/.env.production` (в образ не копируется, см. `web/.dockerignore`); порт генератора наружу не публикуется, `shm_size: 256mb`.

## Важные особенности архитектуры

- **Blender держится запущенным.** `gen/web.py` при старте поднимает `blender -b basket.blend --factory-startup --python-exit-code 1 --python worker.py`; воркер прогревает glTF-аддон и только потом создаёт сокет (его появление = «готов»). Это избавляет от ~секунд холодного старта на каждый запрос.
- **Общая корзина для всех клиентов:** в `web.py` одно состояние в памяти процесса — `state` (желаемые x/y/z + `version`, растёт с каждой правкой любого клиента), `ready` (последняя опубликованная модель с GLB в байтах), `last_error`; всё под одним `asyncio.Condition changed`. Стартует с `version=1` и дефолтами клиента (100/100/50). Работает только при одном процессе uvicorn (без `--workers`); `--reload` в dev сбрасывает состояние.
- **Цикл генерации (`generation_loop`)** — единственный, кто говорит с Blender (`bpy` не потокобезопасен, воркер однопоточный): берёт снимок `state`, считает до конца (Blender не прерывается), и если за это время пришла новая правка — результат выбрасывает («устарела» в логе) и сразу считает свежую. Публикует только актуальную модель: значения и модель приходят клиентам **только вместе**, при непрерывном движении слайдера промежуточных моделей нет.
- **Отправка:** у каждого соединения своя `send_loop` — единственная, кто пишет в сокет (поэтому `model` → бинарный кадр идут подряд); снимок берётся под `changed`, отправка вне блокировки, медленный клиент пропускает промежуточное. `ack` объединяются (на 5 быстрых правок может прийти один) — поэтому клиент шлёт свой `seq`, а `ack` возвращает последний. Битые сообщения игнорируются без разрыва. Ошибка актуальной версии рассылается как `{"type":"error",version,error}`; старые ошибки новому клиенту не шлются.
- **`/ws?glb=0`:** gen шлёт только заголовки `model` без бинарного кадра — для клиентов, которым GLB не нужен (MCP).
- **MCP-сервер (`mcp/server.py`)** — ещё один клиент gen: каждый вызов инструмента открывает своё короткое соединение `ws://gen:8080/ws?glb=0` (сразу приходит текущая корзина — своего состояния и переподключений нет). Инструменты: `get_basket()` → `{version,x,y,z}`; `set_basket(x?,y?,z?)` — неуказанные берутся из текущей корзины, диапазоны (x, y 20–256, z 20–150) в схеме через `Field(ge, le)`, шлёт `params`, ждёт `ack` и затем `model` версии ≥ своей (до `RESULT_TIMEOUT` = 60 с): равна — `status: "applied"`, больше — `"superseded"` (перекрыл другой клиент) + корзина, которую видят все. Ожидаемые ошибки — только через `ToolError`: текст прочих исключений SDK v2 скрывает от клиента («Error executing tool …» без подробностей). DNS-rebinding-защита SDK отключена (`TransportSecuritySettings`): иначе принимается только `Host: localhost`, а Host контролирует nginx.
- **Самовосстановление:** если воркер упал, `render()` перезапускает его и повторяет генерацию один раз.
- **Протокол воркера:** одна JSON-строка `{"x","y","z","out"}` → ответ `{"ok":true}` или `{"ok":false,"error":"..."}`.
- **Параметры сцены:** узел `Group Output` в группе нод `_dimensions` (входы с индексами 1, 2, 3 = x, y, z; z — высота); объект `Basket`. Воркер после смены значений вызывает `update_tag()` + `view_layer.update()`, берёт вычисленную сетку через `evaluated_get`/`new_from_object`, экспортирует во временный объект `ExportTemp` (удаляется сразу, т.к. сцена живёт между запросами). Переименование в `basket.blend` сломает `worker.py`.
- **Временные файлы** лежат в `/dev/shm` (общая память; поэтому `shm_size`: 64mb в dev, 256mb в prod); `render()` читает GLB в память и удаляет файл в `finally`.
- **Управление видом:** `OrbitControls` в `ModelViewer.vue` — колесо/щипок масштабируют, ЛКМ/один палец двигают, два пальца (и ПКМ) вращают; автовращение (группы `turntable`) сохранено. Кнопка ⟲ справа вверху вьювера (`resetView`) возвращает камеру, `target` и поворот `turntable` к исходным. Кнопка слева вверху (`toggleFullscreen`) разворачивает область `.canvas-wrap` на весь экран через Fullscreen API (с `webkit`-префиксом); где API нет (iPhone) — CSS-псевдорежим `position: fixed` (класс `is-fullscreen`), выход по той же кнопке или Esc.
- **Внешний вид модели:** материал задаётся на клиенте (`petgMaterial` в `ModelViewer.vue`: зелёный PETG, `MeshPhysicalMaterial`) и подменяет материалы из GLB; слои печати 0,2 мм — процедурный bump в шейдере через `onBeforeCompile` (по мировой Y от дна модели, без UV; `LAYER_*`), гаснут, когда слой меньше 4–8 px, чтобы не было ряби; освещение — нарисованная на canvas «комната» (`makeRoomEnvironment`: холодное окно + тёплая лампа, IBL) + тёплый `DirectionalLight`; оба закреплены в мире (при вращении вида освещение меняется), начальное положение света задаёт `LIGHT_ANGLE`; тон-маппинг ACES, `pixelRatio ≤ 2`. Фон — та же комната в цветах карточек UI (`ROOM_WALLS_BACKGROUND`), размытая (`backgroundBlurriness`); освещение берётся из нейтральной `ROOM_WALLS_LIGHTING`. Под моделью — прозрачный стол принтера 256×256 как у Bambu Lab A1 (`createBed`/`placeBed`, `BED_SIZE`, сетка `BED_CELL` от центра); `depthWrite: false`, чтобы снизу было видно дно. Стол и модель лежат в группе `turntable`, автовращение и `resetView` работают с ней; стороны модели параллельны краям стола.
- **Слайдеры:** x, y и z (z — вертикальный, слева от вьювера) в диапазоне x, y 20–256 (стол A1), z 20–150, по умолчанию 25 (серверной валидации диапазона нет; дефолты FastAPI — 100/100).
- **Клиент (`ModelViewer.vue`):** `connect()` открывает `ws(s)://<host>/ws` (`binaryType = 'arraybuffer'`), при обрыве переподключается через `RECONNECT_DELAY` (пока gen прогревает Blender, nginx отвечает 502 — оверлей «Подключение к генератору…»). При `onopen` версии сбрасываются (gen мог перезапуститься и нумеровать заново). Слайдеры на `@input` вызывают `sendParams()` (`seq` растёт локально). `showModel()` пропускает модели не новее показанной, затем ставит x/y/z в слайдеры, если разрешает `shouldApplyValues(msg)` (не «отдёргивать» слайдер у того, кто его тянет). Спиннер — пока нет первой модели или ждём итог своей правки (`hasUnacked()` / `lastAck > settledVersion`). Журнал — полученные модели и ошибки, `MAX_HISTORY = 20`.
- nginx (`location = /ws`, `proxy_http_version 1.1`, `Upgrade`/`Connection "upgrade"`, `proxy_read_timeout 120s` — uvicorn шлёт ping каждые 20 с) проксирует в `gen:8080/ws`. В dev порт 8080 остаётся опубликованным для отладки, в prod — нет. CORS на стороне FastAPI открыт для всех (`*`), но при проксировании не нужен.
- Vite настроен под Docker/WSL: `host 0.0.0.0`, `usePolling`, алиас `vue` → `vue.esm-bundler.js` (нужен для шаблонов в Blade).

## Соглашения

- Язык комментариев и UI — русский; придерживайся этого при правках.
- Отступы: по `web/.editorconfig` — 4 пробела, но в `gen/*.py`, `ModelViewer.vue`, `nginx.conf`, `Dockerfile` фактически табы — сохраняй стиль файла, который правишь. См. `web/.editorconfig`.
- Новые Vue-компоненты регистрируй глобально в `web/resources/js/app.js` и подключай в Blade через `<tag-name>`.
- БД приложению не нужна: сессии — `cookie`, кэш — `file`, очередь — `sync` (см. `web/.env.example`, `web/.env.production`). `DB_CONNECTION=sqlite` остался в `.env`, но соединение не открывается; `database.sqlite` в образе не создаётся и `migrate` в деплое не запускается. Если появятся данные — вернуть БД на постоянном томе и миграции при старте контейнера.

## Известные шероховатости (учитывай, не «чини» без запроса)

- `deploy.sh`: `StrictHostKeyChecking=no` — допустимо только для учебного стенда.
- В dev `nginx.conf` смонтирован отдельным файлом: после правки `docker compose restart nginx` может оставить старый конфиг (редактор заменяет файл новым inode) — надёжно `docker compose up -d --force-recreate --no-deps nginx`.
- MCP без аутентификации; для подключения из claude.ai нужен HTTPS, которого на VPS пока нет.
- `compose.yml`/`compose.prod.yml`: `depends_on` на `gen` не гарантирует готовность Blender (сокет появляется после прогрева).
- В корне лежат учебные заметки (`dev1.md`, `dev2.md`, `new1.md`, `new2.md`, `review1.md`, `review2.md`) — не часть приложения.
- Нет аутентификации и валидации диапазонов `x`/`y`/`z` на стороне генератора; любой открывший страницу меняет корзину для всех.
