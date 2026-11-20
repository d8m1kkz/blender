# CLAUDE.md

Этот файл — руководство для Claude Code по работе с репозиторием.

## Суть проекта

Учебный проект (без production-требований): веб-приложение, в котором пользователь задаёт параметры `x` и `y`, а сервер процедурно генерирует 3D-модель в Blender (Geometry Nodes) и отдаёт её как `.glb`. Браузер показывает модель через Three.js.

Поток данных:

```
Браузер (Vue + Three.js, ModelViewer.vue)
   │  GET /generate?x=..&y=..   (nginx проксирует в generator:8080, минуя PHP)
   ▼
generator (FastAPI, gen/web.py)  ──unix-сокет /dev/shm/blender.sock──►  Blender-воркер (gen/worker.py)
   ▲                                                                       │ пишет /dev/shm/m_<uuid>.glb
   └──────────────── FileResponse (model/gltf-binary), файл удаляется ◄────┘
```

Laravel (`web/`) сейчас лишь отдаёт страницу `welcome.blade.php` с Vue-компонентом `<model-viewer>`; бэкенд-логики PHP почти нет.

Модель в сцене — корзина (`Basket`); пользовательские `x`/`y` задают её размеры.

## Структура репозитория

| Путь | Назначение |
|---|---|
| `web/` | Laravel 13 (PHP ^8.3, образ на PHP 8.5-fpm), Vite 8, Vue 3, Tailwind 4, Three.js |
| `web/resources/js/app.js` | Точка входа: регистрирует компоненты `counter-control` и `model-viewer`, монтирует `#app` |
| `web/resources/js/components/ModelViewer.vue` | Основной UI: слайдеры/поля x, y, очередь запросов, рендер GLB через `GLTFLoader` |
| `web/resources/js/components/CounterControl.vue` | Демо-компонент счётчика (не используется в `welcome.blade.php`) |
| `web/resources/views/welcome.blade.php` | Рабочая страница с `<model-viewer>`; `welcome1.blade.php` — запасной/старый вариант |
| `gen/` | Python-сервис генерации: `web.py` (FastAPI), `worker.py` (долгоживущий Blender), `basket.blend` (сцена), `.dockerignore` |
| `nginx/nginx.conf` | Конфиг nginx: `location = /generate` → `generator:8080`, остальное → php-fpm (`php:9000`), root `public/` |
| `compose.yml` | Dev-окружение: сервисы `node`, `nginx`, `php`, `generator` (код монтируется томами) |
| `compose.prod.yml` | Prod-окружение: код внутри образов, без `node` и bind-mount'ов, `restart: unless-stopped`, том `app-storage`/`app-database` |
| `web/Dockerfile` | Multi-stage: `base` → `vendor` → `assets` (Vite build) → `app` (php-fpm) и `web` (nginx со статикой) |
| `gen/Dockerfile` | Multi-stage: `base` (Blender + FastAPI) → `prod` (копирует исходники) и `dev` (последний stage, по умолчанию; `--reload`, код томом) |
| `deploy.sh` | Заготовка деплоя по SSH на `ubuntu@<IP>` (пока только `cloud-init status --wait` и `apt clean`) |

## Запуск и команды

Всё работает в Docker Compose из корня:

```bash
docker compose up --build                                  # dev (compose.yml): node (Vite :5173), nginx (:80), php, generator (:8080)
docker compose -f compose.prod.yml build                   # prod
docker compose -f compose.prod.yml up -d
```

- Сайт: http://localhost (nginx), Vite HMR: :5173, генератор: http://localhost:8080/?x=25&y=25 (через nginx — `/generate?x=..&y=..`)
- **Перед сборкой `generator`** в `gen/` должен лежать `blender-linux-x64.tar.xz` (Dockerfile подключает его через `--mount=type=bind`). Архив в `.gitignore` (`*.tar.xz`), в git его нет.
- Первоначальная настройка Laravel (из `web/`, внутри php-контейнера): `composer setup` (install, `.env`, `key:generate`, `migrate`, `npm install`, `npm run build`).
- Фронтенд: `npm run dev` / `npm run build` (из `web/`). Node-сервис в compose запускает `npm run dev` сам.
- Тесты PHP: `php artisan test` (PHPUnit, `web/tests/`); сейчас только примерные тесты.
- Образы: `d8m1kkz/blender-php`, `d8m1kkz/blender-gen`, `d8m1kkz/blender-nginx` (последний только в prod). Dev использует `target: base` для php и stage `dev` для generator.
- Prod: секреты (`APP_KEY` и т.д.) — в `web/.env.production` (в образ не копируется, см. `web/.dockerignore`); порт генератора наружу не публикуется, `shm_size: 256mb`.

## Важные особенности архитектуры

- **Blender держится запущенным.** `gen/web.py` при старте поднимает `blender -b basket.blend --factory-startup --python-exit-code 1 --python worker.py`; воркер прогревает glTF-аддон и только потом создаёт сокет (его появление = «готов»). Это избавляет от ~секунд холодного старта на каждый запрос.
- **Один запрос за раз:** `asyncio.Lock` в `web.py`; `bpy` не потокобезопасен, воркер работает в главном потоке и обслуживает соединения последовательно.
- **Самовосстановление:** если воркер упал, `web.py` перезапускает его и повторяет запрос один раз.
- **Протокол воркера:** одна JSON-строка `{"x","y","out"}` → ответ `{"ok":true}` или `{"ok":false,"error":"..."}`.
- **Параметры сцены:** узел `Group Output` в группе нод `_dimensions` (входы с индексами 1 и 2 = x, y); объект `Basket`. Воркер после смены значений вызывает `update_tag()` + `view_layer.update()`, берёт вычисленную сетку через `evaluated_get`/`new_from_object`, экспортирует во временный объект `ExportTemp` (удаляется сразу, т.к. сцена живёт между запросами). Переименование в `basket.blend` сломает `worker.py`.
- **Временные файлы** лежат в `/dev/shm` (общая память; поэтому `shm_size`: 64mb в dev, 256mb в prod) и удаляются через `BackgroundTask`.
- **Слайдеры:** x и y в диапазоне 20–100, по умолчанию 25/25 (серверной валидации диапазона нет; дефолты FastAPI — 100/100).
- **Очередь на клиенте:** в `ModelViewer.vue` при новом запросе все *ожидающие* задания отбрасываются, остаётся текущее и последнее (debounce по смыслу); история ограничена `MAX_HISTORY = 50`.
- `ModelViewer.vue` обращается к относительному `/generate`; nginx (`location = /generate`, `proxy_read_timeout 120s`, `proxy_buffering off`) проксирует его в `generator:8080` (корень FastAPI `/`). В dev порт 8080 остаётся опубликованным для отладки, в prod — нет. CORS на стороне FastAPI открыт для всех (`*`), но при проксировании не нужен.
- Vite настроен под Docker/WSL: `host 0.0.0.0`, `usePolling`, алиас `vue` → `vue.esm-bundler.js` (нужен для шаблонов в Blade).

## Соглашения

- Язык комментариев и UI — русский; придерживайся этого при правках.
- Отступы: по `web/.editorconfig` — 4 пробела, но в `gen/*.py`, `ModelViewer.vue`, `nginx.conf`, `Dockerfile` фактически табы — сохраняй стиль файла, который правишь. См. `web/.editorconfig`.
- Новые Vue-компоненты регистрируй глобально в `web/resources/js/app.js` и подключай в Blade через `<tag-name>`.
- БД по умолчанию SQLite, сессии/кэш/очередь — `database` (см. `web/.env.example`); миграции стандартные.

## Известные шероховатости (учитывай, не «чини» без запроса)

- `deploy.sh`: опечатка `set -euo pipefailс` (кириллическая «с») внутри heredoc и опечатка в сообщении; `StrictHostKeyChecking=no` — допустимо только для учебного стенда.
- `compose.yml`/`compose.prod.yml`: `depends_on` на `generator` не гарантирует готовность Blender (сокет появляется после прогрева).
- В корне лежат учебные заметки (`dev1.md`, `dev2.md`, `new1.md`, `new2.md`, `review1.md`, `review2.md`) — не часть приложения.
- Нет аутентификации и валидации диапазонов `x`/`y` на стороне генератора.
