# AGENTS.md

## Subagent delegation

Agents SHOULD prefer the opencode-subagent-delegation skill (numbered `itemNN.md`
specs in a gitignored scratch dir, one agent per item, agents verify their work
and `git add` but NEVER `git commit`) wherever doing so does not overwrite these
instructions or the user's prior statements of preference.

## Local serving

Local browser testing MUST use `make serve` / `make stop` (nginx, pidfile
`.tmp/nginx.pid`). Agents MUST NOT launch ad-hoc servers (e.g.
`python3 -m http.server`) and MUST NOT leave any server running when their
task ends.
