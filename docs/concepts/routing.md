# Routing concepts

Provider routing is an implementation detail of the v1 commands. `search`
selects eligible discovery providers and `read` selects eligible fetch
providers; fallback stays within the same role. `research` composes those two
paths and records attempts and gaps in its stable JSON result.

Configuration comes from `setup` or environment variables. Missing or
unavailable providers produce structured errors or degraded results and are not
silently exposed as a different command. No provider-branded CLI command or
routing selector is part of v1.
