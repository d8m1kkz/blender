# Образ nginx: конфиг и статика из public_html/. PHP-код и секреты сюда не попадают.
# app — это образ сервиса php: compose подключает его через additional_contexts (см. compose.yml),
# поэтому собирать через docker compose, а не отдельным docker build.
FROM nginx:stable
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=app /var/www/html/public_html /var/www/html/public_html
