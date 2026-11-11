# Plan item 27: the headers every proxied /api/ request carries (included by
# both /api/ locations in nginx.conf; the .inc name keeps the image's
# `include conf.d/*.conf` from loading it on its own).
#
# X-Forwarded-For is set, not appended: $remote_addr, the address nginx itself
# saw, replaces anything the browser sent, so a client cannot plant an address
# for the api's rate limiter. The api trusts this header only from nginx's own
# address (FORWARDED_ALLOW_IPS in docker-compose.yml).
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
