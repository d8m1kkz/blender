# ---- base: окружение PHP ----
# Расширения, нужные Laravel, уже есть в официальном образе; БД приложению не нужна, свои не доставляем.
FROM php:8.5-fpm AS base

# unzip нужен Composer для распаковки пакетов
RUN apt-get update && apt-get install -y --no-install-recommends unzip \
	&& rm -rf /var/lib/apt/lists/*
COPY --from=composer:2 /usr/bin/composer /usr/local/bin/composer

WORKDIR /var/www/html

# ---- vendor: PHP-зависимости без dev-пакетов ----
FROM base AS vendor
COPY composer.json composer.lock ./
RUN composer install --no-dev --no-scripts --no-autoloader --prefer-dist --no-interaction

# ---- assets: сборка Vite (public_html/build) ----
FROM node:22-alpine AS assets
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY vite.config.js ./
COPY resources ./resources
COPY public_html ./public_html
RUN npm run build

# ---- app: образ php (php-fpm с исходниками внутри) ----
FROM base AS app
COPY . .
COPY --from=vendor /var/www/html/vendor ./vendor
COPY --from=assets /app/public_html/build ./public_html/build
RUN composer dump-autoload --optimize --no-dev \
	&& mkdir -p storage/framework/cache storage/framework/sessions storage/framework/views storage/logs bootstrap/cache \
	&& chown -R www-data:www-data storage bootstrap/cache
