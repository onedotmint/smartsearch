# Pi Agent Adapter

Install the Smart Search CLI, then install the native Pi package:

```sh
pi install npm:@onedotmint/pi-smart-search
```

The package registers `web_search`, `web_read`, and `web_research` and delegates
through Pi's native tool adapter to the installed `smart-search` executable.
