# Fixture structure for local testing

To test the analyzer locally with real Cloudways logs, create this structure:

tests/fixtures/cloudways-apps/<APPID>/
  logs/
    backend_wordpress*.access.log
    static_wordpress*.access.log
    php-app.access.log*
    php-app.slow.log*
    wp-cron.log
    backend_wordpress*.error.log (optional)
  public_html/
    wp-config.php

Notes:

- Logs can include rotated (.1) and compressed (.gz) files.
- wp-config.php can be an empty file.
- The analyzer will treat each <APPID> directory as one application.

Run locally:

PYTHONPATH=src python3 -m serverbottleneck.cli \
  --applications-root tests/fixtures/cloudways-apps
