# Pi Agent Adapter

Install the stable Smart Search CLI and native Pi package:

```sh
npm install -g @onedotmint/smart-search@latest
pi install npm:@onedotmint/pi-smart-search@latest
```

The package registers exactly `web_search`, `web_read`, and `web_research` and
delegates through Pi's native tool adapter to the installed `smart-search`
executable. The tools return the v1 envelope:
`{version, operation, status, data, attempts, warnings, error}`.
