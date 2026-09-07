# AgentGate deploy Makefile
#
# Design: docs/superpowers/service/specs/deploy.md, with the following
# decisions settled by the product owner (overriding that document's
# open questions):
#
#   - Auth: SSH key via a host alias in the OPERATOR's own ~/.ssh/config.
#     Before the first `make deploy`, the operator adds an entry like:
#
#         Host agentgate
#             HostName <server ip or hostname>
#             User <deploy user>
#             IdentityFile ~/.ssh/<key>
#
#     and runs `ssh-copy-id agentgate` once, by hand. After that,
#     `make deploy` needs no password -- ssh and rsync below inherit
#     host, user, and key entirely from that alias. This file must never
#     carry an IP address, an IdentityFile, or a password/token -- if
#     you find yourself adding one, stop: it belongs in ~/.ssh/config,
#     not here.
#   - Build happens on the server (`docker compose up -d --build`), not
#     locally with a push to a registry -- see deploy.md's open question 2.
#   - One server, no staging/prod split, no CI trigger -- just a local
#     `make deploy` run by a human. The user runs this target themselves;
#     it is never invoked automatically.
#
# `DEPLOY_HOST` names the ~/.ssh/config alias, not a hostname or IP.
DEPLOY_HOST ?= agentgate
DEPLOY_DIR ?= /opt/agentgate
# Where the static landing page (../frontend/site) lands on the server;
# docker-compose.deploy.yml bind-mounts it into Caddy.
SITE_DIR ?= /opt/openmagi-site
HEALTHZ_PORT ?= 8400
# The only branch that ships. `make deploy` refuses any other checkout so a
# feature branch cannot reach the server by accident (it happened once);
# override knowingly with DEPLOY_BRANCH=<branch>.
DEPLOY_BRANCH ?= main
# Set by `deploy-main` for the throwaway worktree it deploys from: there
# HEAD is detached, so the branch-name check is replaced by "HEAD is exactly
# this ref". Not meant to be set by hand.
DEPLOY_REF ?=

# Server-side compose invocation: the base file plus the server-only
# overlay (Caddy). Every remote target goes through this so nobody runs
# `docker compose` on the server with only half the stack.
COMPOSE := docker compose -f docker-compose.yml -f docker-compose.deploy.yml

.PHONY: deploy deploy-main logs ps rollback check-clean key keys key-revoke

# Refuse to ship anything but a clean checkout of $(DEPLOY_BRANCH):
# `make deploy` deploys committed code from the release branch only. Scoped to `service/` and `../frontend/`, since that is
# exactly what gets rsynced below -- uncommitted changes elsewhere in
# the repo do not block a deploy.
check-clean:
	@if [ -n "$(DEPLOY_REF)" ]; then \
		if [ "$$(git rev-parse HEAD)" != "$$(git rev-parse $(DEPLOY_REF))" ]; then \
			echo "error: HEAD is not $(DEPLOY_REF) -- deploy-main ships that ref only." >&2; \
			exit 1; \
		fi; \
	else \
		branch=$$(git rev-parse --abbrev-ref HEAD); \
		if [ "$$branch" != "$(DEPLOY_BRANCH)" ]; then \
			echo "error: on branch '$$branch' -- deploys ship '$(DEPLOY_BRANCH)' only. Check it out first, or run 'make deploy-main' from any checkout (or set DEPLOY_BRANCH=$$branch knowingly)." >&2; \
			exit 1; \
		fi; \
	fi
	@if [ -n "$$(git status --porcelain -- . ../frontend)" ]; then \
		echo "error: service/ or frontend/ has uncommitted changes -- refusing to deploy a dirty tree. Commit or stash first." >&2; \
		exit 1; \
	fi

# make deploy:
#   1. check-clean (above)
#   2. rsync the committed service/ tree to the server, then ../frontend/site
#      to $(SITE_DIR) for Caddy to serve. Exclusions come
#      from .gitignore (one list, not three); two stay explicit: `.env`
#      because the server's secrets must survive even if someone edits
#      .gitignore, and `.previous_gate_image` because it is not ignored
#      and `--delete` would remove the rollback pointer.
#   3. docker compose up -d --build on the server (builds from the
#      synced source, no local build/push/registry involved)
#   4. apply migrations inside the running `gate` container
#   5. curl /healthz on the server; a non-zero exit fails the deploy
#   6. curl /healthz over HTTPS from here, with retries: on the very first
#      deploy Caddy is still obtaining the certificate when the server-side
#      check passes, and the TLS handshake fails for up to a minute
#
# Nothing here carries a secret: ssh/rsync resolve $(DEPLOY_HOST) purely
# through the operator's ~/.ssh/config alias.
deploy: check-clean
	@echo "==> syncing service/ to $(DEPLOY_HOST):$(DEPLOY_DIR)"
	rsync -az --delete \
		--filter=':- .gitignore' \
		--exclude='.env' \
		--exclude='.previous_gate_image' \
		./ $(DEPLOY_HOST):$(DEPLOY_DIR)/
	@echo "==> syncing frontend/site/ to $(DEPLOY_HOST):$(SITE_DIR)"
	rsync -az --delete \
		--filter=':- .gitignore' \
		../frontend/site/ $(DEPLOY_HOST):$(SITE_DIR)/
	@echo "==> building and starting containers on $(DEPLOY_HOST)"
	ssh $(DEPLOY_HOST) 'set -e; cd $(DEPLOY_DIR) && \
		PREV_IMAGE=$$($(COMPOSE) images -q gate 2>/dev/null || true); \
		if [ -n "$$PREV_IMAGE" ]; then echo "$$PREV_IMAGE" > .previous_gate_image; fi; \
		GIT_SHA=$(shell git rev-parse HEAD) $(COMPOSE) up -d --build'
	@# Migrations run inside the gate container on start (see Dockerfile CMD);
	@# a second `alembic upgrade head` here raced it and lost on 0008/0009
	@# (DuplicateObjectError), so the health check below is the migration gate.
	@echo "==> health check (polling /healthz up to 30s)"
	@if ssh $(DEPLOY_HOST) 'for i in $$(seq 1 30); do curl -fsS http://localhost:$(HEALTHZ_PORT)/healthz >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1'; then \
		echo "==> deploy OK on the server; checking from here over HTTPS"; \
		PUBLIC_HOST=$$(ssh $(DEPLOY_HOST) "sed -n 's/^AGENTGATE_PUBLIC_HOST=//p' $(DEPLOY_DIR)/.env"); \
		curl -fsS --retry 12 --retry-delay 5 --retry-all-errors "https://$$PUBLIC_HOST/healthz" \
			&& echo && echo "==> https://$$PUBLIC_HOST is up"; \
		SITE_HOST=$$(ssh $(DEPLOY_HOST) "sed -n 's/^AGENTGATE_SITE_HOSTS=//p' $(DEPLOY_DIR)/.env" | awk '{print $$1}'); \
		curl -fsS --retry 12 --retry-delay 5 --retry-all-errors -o /dev/null "https://$$SITE_HOST/" \
			&& echo "==> https://$$SITE_HOST is up"; \
	else \
		echo "error: /healthz did not come up within 30s -- deploy considered FAILED." >&2; \
		echo "       inspect with 'make logs' / 'make ps'; consider 'make rollback'." >&2; \
		exit 1; \
	fi

# Tail logs from the server.
# make deploy-main: `make deploy` from any checkout, on any branch, dirty or
# not. Fetches origin/main, checks it out detached into a throwaway
# worktree, runs `deploy` there and removes the worktree even when the
# deploy fails. What ships is exactly origin/main -- never the local tree,
# so a stale or feature-branch checkout cannot leak into the server, and
# no permanent `main` worktree has to exist for deploys.
deploy-main:
	@git fetch -q origin main
	@tmp=$$(mktemp -d "$${TMPDIR:-/tmp}/agentgate-deploy.XXXXXX"); \
	echo "==> checking out origin/main into $$tmp"; \
	git worktree add -q --detach "$$tmp" origin/main || exit 1; \
	$(MAKE) -C "$$tmp/service" deploy DEPLOY_REF=origin/main; status=$$?; \
	git worktree remove --force "$$tmp"; \
	exit $$status

logs:
	ssh $(DEPLOY_HOST) 'cd $(DEPLOY_DIR) && $(COMPOSE) logs -f'

# Container status on the server.
ps:
	ssh $(DEPLOY_HOST) 'cd $(DEPLOY_DIR) && $(COMPOSE) ps'

# Best-effort rollback to the image that was running before the last
# `make deploy`. `deploy` records that image's id in
# `$(DEPLOY_DIR)/.previous_gate_image` before rebuilding; rollback
# re-tags it onto the name docker compose expects for the `gate`
# service and restarts from it without rebuilding.
#
# This keeps exactly one generation of history (the image from
# immediately before the last deploy) -- it is not a release history,
# just enough to undo the most recent deploy if it broke something.
# There is no automatic rollback on a failed health check; the operator
# runs this by hand after checking `make logs`.
rollback:
	ssh $(DEPLOY_HOST) 'set -e; cd $(DEPLOY_DIR) && \
		if [ ! -f .previous_gate_image ]; then \
			echo "error: no recorded previous image on the server -- nothing to roll back to" >&2; \
			exit 1; \
		fi; \
		PREV=$$(cat .previous_gate_image); \
		TARGET=$$($(COMPOSE) config --images gate); \
		docker tag "$$PREV" "$$TARGET" && \
		$(COMPOSE) up -d --no-build gate'
	@echo "==> rollback applied, checking health"
	ssh $(DEPLOY_HOST) 'curl -fsS http://localhost:$(HEALTHZ_PORT)/healthz'

# API keys on the deployed server. The `keys` CLI needs Postgres, so it only
# runs inside the `gate` container; these targets run it there over the same
# SSH alias `deploy` uses. `-T` keeps stdout clean: `make key` prints the
# plaintext key once, exactly as the CLI does, and nothing here echoes or
# stores it. Design: docs/superpowers/service/specs/2026-09-07-remote-key-issuance-design.md
KEYS_CLI := $(COMPOSE) exec -T gate uv run python -m agentgate keys

# make key LABEL=<who> [EXPIRES=90d]
key:
	$(if $(LABEL),,$(error LABEL is required, e.g. make key LABEL=kilo-ci [EXPIRES=90d]))
	@ssh $(DEPLOY_HOST) "cd $(DEPLOY_DIR) && $(KEYS_CLI) create --label '$(LABEL)'$(if $(EXPIRES), --expires $(EXPIRES))"

# List issued keys (ids and metadata only; the CLI never shows a key or its hash).
keys:
	@ssh $(DEPLOY_HOST) "cd $(DEPLOY_DIR) && $(KEYS_CLI) list"

# make key-revoke ID=<key_id>; revocation takes effect within the key-check cache TTL.
key-revoke:
	$(if $(ID),,$(error ID is required, e.g. make key-revoke ID=<key_id> (see make keys)))
	@ssh $(DEPLOY_HOST) "cd $(DEPLOY_DIR) && $(KEYS_CLI) revoke $(ID)"
