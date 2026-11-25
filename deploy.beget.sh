#!/usr/bin/env bash
# Первый деплой сайта (php/) на Beget в пустую папку ~/blender.
# gen (Blender) здесь не запускается: он должен жить отдельно, по https.
set -euo pipefail
cd "$(dirname "$0")/php"

# Собрать JS и CSS локально: на Beget нет Node
npm run build

# Скопировать нужное (hot и fonts-manifest.dev.json нужны только в разработке)
rsync -rlptz --exclude=hot --exclude=fonts-manifest.dev.json public_html/ beget:blender/public_html/
rsync -rlptz app bootstrap config routes beget:blender/
rsync -rlptzR resources/views beget:blender/
rsync -rlptz artisan composer.json composer.lock beget:blender/
scp .env.beget beget:blender/.env   # настройки (APP_URL, GEN_ORIGINS…) лежат в php/.env.beget

# На сервере: поставить зависимости, создать папки и выдать права на запись.
# Команды composer-phpX.Y на Beget — это Composer 1, а Laravel 13 нужен Composer 2: скачиваем composer.phar
ssh beget '[ -f composer.phar ] || curl -fsSL https://getcomposer.org/download/latest-2.x/composer.phar -o composer.phar
	cd blender &&
	php8.5 ~/composer.phar install --no-dev --optimize-autoloader &&
	mkdir -p storage/framework/{cache,sessions,views} storage/logs &&
	chmod -R 775 storage bootstrap/cache'

echo "Готово: https://d8m1k.bget.ru"
