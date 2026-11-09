# CLAUDE.md

Этот файл — руководство для Claude Code по работе с репозиторием.

## Суть проекта

Учебный проект (без production-требований): веб-приложение, в котором пользователь задаёт параметры `x` и `y`, а сервер процедурно генерирует 3D-модель в Blender (Geometry Nodes) и отдаёт её как `.glb`. Браузер показывает модель через Three.js.

Поток данных:

```
Браузер (Vue + Three.js, ModelViewer.vue)
   │  GET http://localhost:8080/?x=..&y=..   (напрямую, минуя nginx/PHP)
   ▼
generator (FastAPI, gen/web.py)  ──unix-сокет /dev/shm/blender.sock──►  Blender-воркер (gen/worker.py)
   ▲                                                                       │ пишет /dev/shm/m_<uuid>.glb
   └──────────────── FileResponse (model/gltf-binary), файл удаляется ◄────┘
```

Laravel (`web/`) сейчас лишь отдаёт страницу `welcome.blade.php` с Vue-компонентом `<model-viewer>`; бэкенд-логики PHP почти нет.

## Структура репозитория

| Путь | Назначение |
|---|---|
| `web/` | Laravel 13 (PHP ^8.3, образ на PHP 8.5-fpm), Vite 8, Vue 3, Tailwind 4, Three.js |
| `web/resources/js/app.js` | Точка входа: регистрирует компоненты `counter-control` и `model-viewer`, монтирует `#app` |
| `web/resources/js/components/ModelViewer.vue` | Основной UI: слайдеры/поля x, y, очередь запросов, рендер GLB через `GLTFLoader` |
| `web/resources/js/components/CounterControl.vue` | Демо-компонент счётчика (не используется в `welcome.blade.php`) |
| `web/resources/views/welcome.blade.php` | Рабочая страница с `<model-viewer>`; `welcome1.blade.php` — запасной/старый вариант |
| `gen/` | Python-сервис генерации: `web.py` (FastAPI), `worker.py` (долгоживущий Blender), `generate.py` (старый одноразовый скрипт, не используется), `cube.blend` (сцена) |
| `nginx/nginx.conf` | Конфиг nginx → php-fpm (`php:9000`), root `public/` |
| `docker-compose.yml` | Сервисы `node`, `nginx`, `php`, `generator` |
| `deploy.sh` | Заготовка деплоя по SSH на `ubuntu@<IP>` (пока только `cloud-init status --wait` и `apt clean`) |

## Запуск и команды

Всё работает в Docker Compose из корня:

```bash
docker compose up --build     # node (Vite :5173), nginx (:80), php, generator (:8080)
```

- Сайт: http://localhost (nginx), Vite HMR: :5173, генератор: http://localhost:8080/?x=100&y=100
- **Перед сборкой `generator`** в `gen/` должен лежать `blender-linux-x64.tar.xz` (Dockerfile подключает его через `--mount=type=bind`). Архив в `.gitignore` (`*.tar.xz`), в git его нет.
- Первоначальная настройка Laravel (из `web/`, внутри php-контейнера): `composer setup` (install, `.env`, `key:generate`, `migrate`, `npm install`, `npm run build`).
- Фронтенд: `npm run dev` / `npm run build` (из `web/`). Node-сервис в compose запускает `npm run dev` сам.
- Тесты PHP: `php artisan test` (PHPUnit, `web/tests/`); сейчас только примерные тесты.
- Образы: `d8m1kkz/blender-php`, `d8m1kkz/blender-gen` (собираются из compose).

## Важные особенности архитектуры

- **Blender держится запущенным.** `gen/web.py` при старте поднимает `blender -b cube.blend --python worker.py`; воркер прогревает glTF-аддон и только потом создаёт сокет (его появление = «готов»). Это избавляет от ~секунд холодного старта на каждый запрос.
- **Один запрос за раз:** `asyncio.Lock` в `web.py`; `bpy` не потокобезопасен, воркер работает в главном потоке и обслуживает соединения последовательно.
- **Самовосстановление:** если воркер упал, `web.py` перезапускает его и повторяет запрос один раз.
- **Протокол воркера:** одна JSON-строка `{"x","y","out"}` → ответ `{"ok":true}` или `{"ok":false,"error":"..."}`.
- **Параметры сцены:** узел `Group Output` в группе нод `_params` (входы 0 и 1 = x, y); объект `Vert` с модификатором `GeometryNodes`. Переименование в `cube.blend` сломает `worker.py`.
- **Временные файлы** лежат в `/dev/shm` (общая память; поэтому в compose `shm_size: 256mb`) и удаляются через `BackgroundTask`.
- **Очередь на клиенте:** в `ModelViewer.vue` при новом запросе все *ожидающие* задания отбрасываются, остаётся текущее и последнее (debounce по смыслу); история ограничена `MAX_HISTORY = 50`.
- URL генератора захардкожен в `ModelViewer.vue` (`http://localhost:8080`); CORS на стороне FastAPI открыт для всех (`*`).
- Vite настроен под Docker/WSL: `host 0.0.0.0`, `usePolling`, алиас `vue` → `vue.esm-bundler.js` (нужен для шаблонов в Blade).

## Соглашения

- Язык комментариев и UI — русский; придерживайся этого при правках.
- Отступы: в Python/Vue — 4 пробела; в нескольких файлах (`nginx.conf`, `Dockerfile`, `generate.py`) встречаются табы — сохраняй стиль файла, который правишь. См. `web/.editorconfig`.
- Новые Vue-компоненты регистрируй глобально в `web/resources/js/app.js` и подключай в Blade через `<tag-name>`.
- БД по умолчанию SQLite, сессии/кэш/очередь — `database` (см. `web/.env.example`); миграции стандартные.

## Известные шероховатости (учитывай, не «чини» без запроса)

- `deploy.sh`: опечатка `set -euo pipefailс` (кириллическая «с») внутри heredoc и опечатка в сообщении; `StrictHostKeyChecking=no` — допустимо только для учебного стенда.
- `gen/generate.py` дублирует логику `worker.py` и не вызывается.
- `docker-compose.yml`: сервису `php` нужен `php-fpm`-образ, но `depends_on` на `generator` не гарантирует готовность Blender (сокет появляется после прогрева).
- Нет аутентификации и валидации диапазонов `x`/`y` на стороне генератора.
