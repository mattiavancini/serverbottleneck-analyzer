# Local Fixture Structure

Point the CLI at a directory that mimics `~/applications`:

```text
fixture-root/
  app-one/
    logs/
      backend_wordpress-*.access.log
      static_wordpress-*.access.log
      php-app.access.log
      php-app.slow.log
      php-app.slow.log.1
      php-app.slow.log.2.gz
      wp-cron.log
      backend_wordpress-*.error.log
    public_html/
      wp-config.php
  app-two/
    logs/
      ...
    public_html/
      ...
```

Run against the fixture with:

```bash
PYTHONPATH=src python3 -m serverbottleneck.cli --applications-root /path/to/fixture-root
```

Slow-log fixtures should use PHP-FPM block format, with each event starting like:

```text
[15-Mar-2026 00:00:21]  [pool gdbyygwgxg] pid 2124046
script_filename = /home/example/app/public_html/wp-cron.php
...
```
