# Страница bio.madirahman.com

Статическая страница с портретом и двумя ссылками. Она обслуживается установленным на VPS Caddy напрямую, без отдельного контейнера и без изменения существующего сайта.

DNS-запись `bio.madirahman.com` уже указывает на `46.225.70.253`. Чтобы опубликовать страницу после обновления репозитория на сервере:

```bash
cd /opt/tgbotmadi
git pull --ff-only origin main
```

Добавьте блок из `deploy/Caddyfile-bio.example` в `/etc/caddy/Caddyfile`. Не заменяйте этим блоком существующий файл целиком. Затем:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -I https://bio.madirahman.com/
```

Ожидается `HTTP/2 200`. Если Caddy ещё получает сертификат, проверьте логи: `sudo journalctl -u caddy -n 50 --no-pager`, затем повторите запрос.

При следующем изменении страницы достаточно `git pull --ff-only origin main`: Caddy читает статические файлы непосредственно из `/opt/tgbotmadi/bio`.
