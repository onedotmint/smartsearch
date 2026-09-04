# Smart Search for Pi

Install the Pi package after installing the `smart-search` CLI:

```sh
pi install npm:@onedotmint/pi-smart-search
```

The extension provides `web_search`, `web_read`, and `web_research`. It invokes
that installed CLI and returns its v1 JSON results.

Run the offline package checks from this directory:

```sh
npm install
npm test
npm run typecheck
npm pack --dry-run
```
