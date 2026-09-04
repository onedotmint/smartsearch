# Smart Search for Pi

Install the stable Smart Search CLI and the native Pi package:

```sh
npm install -g @onedotmint/smart-search@latest
pi install npm:@onedotmint/pi-smart-search@latest
```

The extension provides `web_search`, `web_read`, and `web_research`. It invokes
the installed v1 CLI and returns its stable JSON envelope.

Run the offline package checks from this directory:

```sh
npm install
npm test
npm run typecheck
npm pack --dry-run
```
