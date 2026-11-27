# Образ nginx: конфиг и статика из public_html/.
# app — это образ сервиса php: compose подключает его через additional_contexts (см. compose.yml),
# поэтому собирать через docker compose.
FROM nginx:stable
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=app /var/www/html/public_html /var/www/html/public_html
