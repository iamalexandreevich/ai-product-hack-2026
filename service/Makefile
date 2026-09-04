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
HEALTHZ_PORT ?= 8400

# Server-side compose invocation: the base file plus the server-only
# overlay (Caddy). Every remote target goes through this so nobody runs
# `docker compose` on the server with only half the stack.
COMPOSE := docker compose -f docker-compose.yml -f docker-compose.deploy.yml

.PHONY: deploy logs ps rollback check-clean

# Refuse to ship a dirty working tree: `make deploy` deploys committed
# code only. Scoped to `service/` (this Makefile's own directory), since
# that is exactly what gets rsynced below -- uncommitted changes
# elsewhere in the repo do not block a service deploy.
check-clean:
	@if [ -n "$$(git status --porcelain -- .)" ]; then \
		echo "error: service/ has uncommitted changes -- refusing to deploy a dirty tree. Commit or stash first." >&2; \
		exit 1; \
	fi

# make deploy:
#   1. check-clean (above)
#   2. rsync the committed service/ tree to the server. Exclusions come
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
	@echo "==> building and starting containers on $(DEPLOY_HOST)"
	ssh $(DEPLOY_HOST) 'set -e; cd $(DEPLOY_DIR) && \
		PREV_IMAGE=$$($(COMPOSE) images -q gate 2>/dev/null || true); \
		if [ -n "$$PREV_IMAGE" ]; then echo "$$PREV_IMAGE" > .previous_gate_image; fi; \
		GIT_SHA=$(shell git rev-parse HEAD) $(COMPOSE) up -d --build && \
		$(COMPOSE) exec -T gate uv run alembic upgrade head'
	@echo "==> health check (polling /healthz up to 30s)"
	@if ssh $(DEPLOY_HOST) 'for i in $$(seq 1 30); do curl -fsS http://localhost:$(HEALTHZ_PORT)/healthz >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1'; then \
		echo "==> deploy OK on the server; checking from here over HTTPS"; \
		PUBLIC_HOST=$$(ssh $(DEPLOY_HOST) "sed -n 's/^AGENTGATE_PUBLIC_HOST=//p' $(DEPLOY_DIR)/.env"); \
		curl -fsS --retry 12 --retry-delay 5 --retry-all-errors "https://$$PUBLIC_HOST/healthz" \
			&& echo && echo "==> https://$$PUBLIC_HOST is up"; \
	else \
		echo "error: /healthz did not come up within 30s -- deploy considered FAILED." >&2; \
		echo "       inspect with 'make logs' / 'make ps'; consider 'make rollback'." >&2; \
		exit 1; \
	fi

# Tail logs from the server.
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
