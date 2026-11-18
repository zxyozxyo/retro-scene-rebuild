Server config for retroscenerebuild.com (Hetzner, Caddy + fail2ban).

- Caddyfile -> /etc/caddy/Caddyfile. Downloads under /files/ need a Referer
  from the site (the app sends one); anything else gets 403.html.
- fail2ban-filter-caddy-rsr.conf -> /etc/fail2ban/filter.d/caddy-rsr.conf
- fail2ban-jail-rsr-leech.conf -> /etc/fail2ban/jail.d/rsr-leech.conf
  100 .rsr an hour or 300 a day per IP -> 24 h ban on ports 80/443.
- robots.txt (keeps crawlers out of /files/) and 403.html come from build_site.py.

Check after a change: caddy validate --config /etc/caddy/Caddyfile;
fail2ban-regex /var/log/caddy/access.log /etc/fail2ban/filter.d/caddy-rsr.conf
