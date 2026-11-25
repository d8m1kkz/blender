# CLAUDE.md

Руководство для Claude Code по работе с репозиторием.

## Суть проекта

Учебный проект (без production-требований). Пользователь задаёт размеры корзины (x, y, z и форму), сервер `gen` строит 3D-модель в Blender (Geometry Nodes) и отдаёт её как GLB, браузер показывает её через Three.js.

```
Браузер (Vue + Three.js) ──WebSocket──► gen (FastAPI) ──► Blender-воркер
```

- `nginx/` — `Dockerfile` и `nginx.conf` сервиса `nginx`: раздаёт статику (копирует её из образа `php`) и передаёт PHP-запросы в `php`.
- `php/` — Laravel + Vite + Vue, сервис `php` (php-fpm с кодом). Laravel почти ничего не делает: отдаёт страницу с компонентом `ModelViewer.vue` и список адресов `gen` (`/gens`).
- `gen/` — сервис генерации: `web.py` (FastAPI), `worker.py` (долгоживущий Blender), `basket.blend` (сцена).
- `scripts/` — замер памяти `gen`: `measure_mem.sh` (запускает `memtest.py` в контейнере), `poll_mem.sh` (опрос `GET /memory`).
- Браузер подключается к выбранному `gen` напрямую (WebSocket и `/limits`); сайт лишь даёт список адресов `GEN_ORIGINS`.

## Принципы

- **`compose.yml` — production, `compose.override.yml` — только отклонения для разработки** (тома с кодом, `ports`, `--reload`, сброс `env_file`). Override подхватывается сам при `docker compose up`; prod запускается с `-f compose.yml`. Что нужно и в prod, добавляй в `compose.yml`.
- **Один образ везде** (`d8m1kkz/blender-nginx`, `d8m1kkz/blender-php`, `d8m1kkz/blender-gen`, тег `latest`): dev и prod различаются только compose-файлом и переменными окружения. Не заводи отдельные теги и `target` для dev.
- **Адреса `gen` — одна переменная `GEN_ORIGINS`** (через запятую, первый по умолчанию), своя в каждом окружении. Браузер ходит к ним напрямую, поэтому адрес должен быть доступен из браузера, а на https-странице — тоже https (иначе mixed content). Если страница открывается по https (Beget, вариант с caddy), `gen` тоже нужен под HTTPS.
- **Общая корзина:** состояние хранится в памяти одного процесса `gen`; клиенты одного `gen` видят одну корзину, клиенты разных `gen` независимы. Работает только с одним процессом uvicorn.
- **Blender держится запущенным**, с ним говорит единственный цикл генерации (`bpy` не потокобезопасен). Устаревшие результаты отбрасываются, клиентам приходит только актуальная модель.
- **Параметры модели и их пределы заданы в одном месте** — реестр `PARAMS` в `worker.py`. `web.py` берёт список имён и пределы у воркера, клиент — запросом `GET /limits`. Для нового параметра нужны строка в `PARAMS` и поле в `ModelViewer.vue` (`params`, `NUMBER_CONTROLS`).
- Имена объектов и нод в `basket.blend` связаны с `worker.py`: переименование сломает воркер.

## Запуск

```bash
docker compose up --build            # dev: nginx (:80), php, gen (:8080), vite (:5173)
(cd php && npm run dev)              # Vite на хосте (нужен Node), из папки php/
docker compose -f compose.yml up -d  # prod (http)
docker compose -f compose.yml -f compose.caddy.yml up -d  # prod с HTTPS через caddy (запасной вариант)
./prepare.sh [сервис…]               # собрать образы и отправить на Docker Hub
./deploy.sh <IP>                     # деплой на VPS
./deploy.beget.sh                    # деплой сайта (без gen) на Beget
scripts/measure_mem.sh [лимит]       # замер памяти gen
```

- Перед сборкой `gen` в `gen/` должен лежать `blender-linux-x64.tar.xz` (в git его нет).
- Секреты Laravel в prod — в `php/.env.beget` (его берут compose-файлы, `deploy.sh` и `deploy.beget.sh`; `.dockerignore` его не исключает, поэтому он попадает в образ); в dev настройки из `php/.env`.
- После правки `nginx.conf` надёжно: `docker compose up -d --force-recreate --no-deps nginx`; после правки `.env` или переменных `php` — то же для `php`.

## Соглашения

- Язык комментариев и интерфейса — русский.
- Отступы задаёт `.editorconfig` в корне. Сохраняй стиль правимого файла.
- Новые Vue-компоненты регистрируй в `php/resources/js/app.js` и подключай в Blade тегом.
- БД приложению не нужна (сессии — cookie, кэш — file, очередь — sync).

## Известные шероховатости (не «чини» без запроса)

- Нет аутентификации: любой открывший страницу меняет корзину для всех.
- `deploy.sh` использует `StrictHostKeyChecking=no` — допустимо только для учебного стенда.
- `depends_on` на `gen` не гарантирует готовность Blender (сокет появляется после прогрева).
- В корне лежат учебные заметки (`dev*.md`, `new*.md`, `review*.md`) — не часть приложения.
