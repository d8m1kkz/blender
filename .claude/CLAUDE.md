# CLAUDE.md

Этот файл — руководство для Claude Code по работе с репозиторием.

## Суть проекта

Учебный проект (без production-требований): веб-приложение, в котором пользователь задаёт параметры `x` и `y`, а сервер процедурно генерирует 3D-модель в Blender (Geometry Nodes) и отдаёт её как `.glb`. Браузер показывает модель через Three.js.

Поток данных:

```
Браузер (Vue + Three.js, ModelViewer.vue)
   │  GET /generate?x=..&y=..   (nginx проксирует в gen:8080, минуя PHP)
   ▼
gen (FastAPI, gen/web.py)  ──unix-сокет /dev/shm/blender.sock──►  Blender-воркер (gen/worker.py)
   ▲                                                                       │ пишет /dev/shm/m_<uuid>.glb
   └──────────────── FileResponse (model/gltf-binary), файл удаляется ◄────┘
```

Laravel (`web/`) сейчас лишь отдаёт страницу `welcome.blade.php` с Vue-компонентом `<model-viewer>`; бэкенд-логики PHP почти нет.

Модель в сцене — корзина (`Basket`); пользовательские `x`/`y` задают её размеры.

## Структура репозитория

| Путь | Назначение |
|---|---|
| `web/` | Laravel 13 (PHP ^8.3, образ на PHP 8.5-fpm), Vite 8, Vue 3, Tailwind 4, Three.js |
| `web/resources/js/app.js` | Точка входа: регистрирует компонент `model-viewer`, монтирует `#app` |
| `web/resources/js/components/ModelViewer.vue` | Основной UI: слайдеры/поля x, y, очередь запросов, рендер GLB через `GLTFLoader` |
| `web/resources/views/welcome.blade.php` | Рабочая страница с `<model-viewer>`; `welcome1.blade.php` — запасной/старый вариант |
| `gen/` | Python-сервис генерации: `web.py` (FastAPI), `worker.py` (долгоживущий Blender), `basket.blend` (сцена), `.dockerignore` |
| `web/nginx.conf` | Конфиг nginx: `location = /generate` → `gen:8080`, остальное → php-fpm (`php:9000`), root `public/`. В dev монтируется томом, в prod копируется в образ `web` (`COPY nginx.conf`) |
| `compose.yml` | Dev-окружение: сервисы `node`, `nginx`, `php`, `gen` (код монтируется томами; у `gen` команда `uvicorn … --reload`) |
| `compose.prod.yml` | Prod-окружение: код внутри образов, без `node` и bind-mount'ов, `restart: unless-stopped`, лимит логов Docker, том `app-storage`; сервисы `nginx`, `php`, `gen` |
| `web/Dockerfile` | Multi-stage: `base` → `vendor` → `assets` (Vite build) → `app` (php-fpm) и `web` (nginx со статикой) |
| `gen/Dockerfile` | Один stage (без `prod`/`dev`): Blender (урезан: без `pip`, `setuptools`, `Cython`, `pxr`, `__pycache__`) + FastAPI, `COPY web.py worker.py basket.blend`, `CMD uvicorn` без `--reload`. Prod использует образ как есть; dev (`compose.yml`) монтирует `./gen:/app` поверх и переопределяет `command` с `--reload` |
| `deploy.sh` | Деплой на VPS: `./deploy.sh <IP>`; локально `docker compose build --push` (с `COMPOSE_FILE=compose.prod.yml`), затем по SSH: доставка compose-файла (как `compose.yml`) и `web/.env.production`, `cloud-init status --wait`, установка Docker, по каждому сервису (`gen`, `php`, `nginx`): `pull`, `up -d --no-build`, `image prune -f` |

## Запуск и команды

Всё работает в Docker Compose из корня:

```bash
docker compose up --build                                  # dev (compose.yml): node (Vite :5173), nginx (:80), php, gen (:8080)
docker compose -f compose.prod.yml build                   # prod
docker compose -f compose.prod.yml up -d
```

- Сайт: http://localhost (nginx), Vite HMR: :5173, генератор: http://localhost:8080/?x=25&y=25 (через nginx — `/generate?x=..&y=..`)
- **Перед сборкой `gen`** в `gen/` должен лежать `blender-linux-x64.tar.xz` (Dockerfile подключает его через `--mount=type=bind`). Архив в `.gitignore` (`*.tar.xz`), в git его нет.
- Первоначальная настройка Laravel (из `web/`, внутри php-контейнера): `composer setup` (install, `.env`, `key:generate`, `migrate`, `npm install`, `npm run build`).
- Фронтенд: `npm run dev` / `npm run build` (из `web/`). Node-сервис в compose запускает `npm run dev` сам.
- Тесты PHP: `php artisan test` (PHPUnit, `web/tests/`); сейчас только примерные тесты.
- Образы: `d8m1kkz/blender-php`, `d8m1kkz/blender-gen`, `d8m1kkz/blender-nginx` (последний только в prod). Dev использует `target: base` для php; у `gen` stage'ей нет — dev и prod различаются только compose-файлом (volume + `command` в dev, код в образе в prod).
- Prod: секреты (`APP_KEY` и т.д.) — в `web/.env.production` (в образ не копируется, см. `web/.dockerignore`); порт генератора наружу не публикуется, `shm_size: 256mb`.

## Важные особенности архитектуры

- **Blender держится запущенным.** `gen/web.py` при старте поднимает `blender -b basket.blend --factory-startup --python-exit-code 1 --python worker.py`; воркер прогревает glTF-аддон и только потом создаёт сокет (его появление = «готов»). Это избавляет от ~секунд холодного старта на каждый запрос.
- **Один запрос за раз:** `asyncio.Lock` в `web.py`; `bpy` не потокобезопасен, воркер работает в главном потоке и обслуживает соединения последовательно.
- **Самовосстановление:** если воркер упал, `web.py` перезапускает его и повторяет запрос один раз.
- **Протокол воркера:** одна JSON-строка `{"x","y","z","out"}` → ответ `{"ok":true}` или `{"ok":false,"error":"..."}`.
- **Параметры сцены:** узел `Group Output` в группе нод `_dimensions` (входы с индексами 1, 2, 3 = x, y, z; z — высота); объект `Basket`. Воркер после смены значений вызывает `update_tag()` + `view_layer.update()`, берёт вычисленную сетку через `evaluated_get`/`new_from_object`, экспортирует во временный объект `ExportTemp` (удаляется сразу, т.к. сцена живёт между запросами). Переименование в `basket.blend` сломает `worker.py`.
- **Временные файлы** лежат в `/dev/shm` (общая память; поэтому `shm_size`: 64mb в dev, 256mb в prod) и удаляются через `BackgroundTask`.
- **Управление видом:** `OrbitControls` в `ModelViewer.vue` — колесо/щипок масштабируют, ЛКМ/один палец двигают, два пальца (и ПКМ) вращают; автовращение (группы `turntable`) сохранено. Кнопка ⟲ справа вверху вьювера (`resetView`) возвращает камеру, `target` и поворот `turntable` к исходным. Кнопка слева вверху (`toggleFullscreen`) разворачивает область `.canvas-wrap` на весь экран через Fullscreen API (с `webkit`-префиксом); где API нет (iPhone) — CSS-псевдорежим `position: fixed` (класс `is-fullscreen`), выход по той же кнопке или Esc.
- **Внешний вид модели:** материал задаётся на клиенте (`petgMaterial` в `ModelViewer.vue`: зелёный PETG, `MeshPhysicalMaterial`) и подменяет материалы из GLB; слои печати 0,2 мм — процедурный bump в шейдере через `onBeforeCompile` (по мировой Y от дна модели, без UV; `LAYER_*`), гаснут, когда слой меньше 4–8 px, чтобы не было ряби; освещение — нарисованная на canvas «комната» (`makeRoomEnvironment`: холодное окно + тёплая лампа, IBL) + тёплый `DirectionalLight`; оба закреплены в мире (при вращении вида освещение меняется), начальное положение света задаёт `LIGHT_ANGLE`; тон-маппинг ACES, `pixelRatio ≤ 2`. Фон — та же комната в цветах карточек UI (`ROOM_WALLS_BACKGROUND`), размытая (`backgroundBlurriness`); освещение берётся из нейтральной `ROOM_WALLS_LIGHTING`. Под моделью — прозрачный стол принтера 256×256 как у Bambu Lab A1 (`createBed`/`placeBed`, `BED_SIZE`, сетка `BED_CELL` от центра); `depthWrite: false`, чтобы снизу было видно дно. Стол и модель лежат в группе `turntable`, автовращение и `resetView` работают с ней; стороны модели параллельны краям стола.
- **Слайдеры:** x, y и z (z — вертикальный, слева от вьювера) в диапазоне x, y 20–256 (стол A1), z 20–150, по умолчанию 25 (серверной валидации диапазона нет; дефолты FastAPI — 100/100).
- **Очередь на клиенте:** в `ModelViewer.vue` при новом запросе все *ожидающие* задания отбрасываются, остаётся текущее и последнее (debounce по смыслу); история ограничена `MAX_HISTORY = 50`.
- `ModelViewer.vue` обращается к относительному `/generate`; nginx (`location = /generate`, `proxy_read_timeout 120s`, `proxy_buffering off`) проксирует его в `gen:8080` (корень FastAPI `/`). В dev порт 8080 остаётся опубликованным для отладки, в prod — нет. CORS на стороне FastAPI открыт для всех (`*`), но при проксировании не нужен.
- Vite настроен под Docker/WSL: `host 0.0.0.0`, `usePolling`, алиас `vue` → `vue.esm-bundler.js` (нужен для шаблонов в Blade).

## Соглашения

- Язык комментариев и UI — русский; придерживайся этого при правках.
- Отступы: по `web/.editorconfig` — 4 пробела, но в `gen/*.py`, `ModelViewer.vue`, `nginx.conf`, `Dockerfile` фактически табы — сохраняй стиль файла, который правишь. См. `web/.editorconfig`.
- Новые Vue-компоненты регистрируй глобально в `web/resources/js/app.js` и подключай в Blade через `<tag-name>`.
- БД приложению не нужна: сессии — `cookie`, кэш — `file`, очередь — `sync` (см. `web/.env.example`, `web/.env.production`). `DB_CONNECTION=sqlite` остался в `.env`, но соединение не открывается; `database.sqlite` в образе не создаётся и `migrate` в деплое не запускается. Если появятся данные — вернуть БД на постоянном томе и миграции при старте контейнера.

## Известные шероховатости (учитывай, не «чини» без запроса)

- `deploy.sh`: `StrictHostKeyChecking=no` — допустимо только для учебного стенда.
- `compose.yml`/`compose.prod.yml`: `depends_on` на `gen` не гарантирует готовность Blender (сокет появляется после прогрева).
- В корне лежат учебные заметки (`dev1.md`, `dev2.md`, `new1.md`, `new2.md`, `review1.md`, `review2.md`) — не часть приложения.
- Нет аутентификации и валидации диапазонов `x`/`y` на стороне генератора.
