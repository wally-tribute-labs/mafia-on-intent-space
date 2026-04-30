# Mafia on Intent Space — common operations
#
# Run from the project root:
#   make help

PYTHON  ?= python3
SCRIPTS := scripts

.PHONY: help
help:
	@echo "Common targets:"
	@echo "  make enroll-host                  - enroll the game-host agent on spacebase1"
	@echo "  make enroll-verify                - enroll three test agents (requester/participant/outsider)"
	@echo "  make smoke-host                   - post + scan smoke test as the host"
	@echo "  make verify-private               - validate shared-space access enforcement"
	@echo "  make enroll-players               - enroll 7 player agents (mafia-player-1 .. mafia-player-7)"
	@echo "  make clean-state                  - DESTRUCTIVE: remove all .intent-space-* workspaces"
	@echo "  make logs                         - tail recent enrollment/smoke artifacts"

.PHONY: enroll-host
enroll-host:
	$(PYTHON) $(SCRIPTS)/enroll_agent.py --agent-name mafia-game-host

.PHONY: smoke-host
smoke-host:
	$(PYTHON) $(SCRIPTS)/smoke_test.py --agent-name mafia-game-host

.PHONY: enroll-verify
enroll-verify:
	$(PYTHON) $(SCRIPTS)/enroll_agent.py --agent-name verify-requester
	$(PYTHON) $(SCRIPTS)/enroll_agent.py --agent-name verify-participant
	$(PYTHON) $(SCRIPTS)/enroll_agent.py --agent-name verify-outsider

.PHONY: verify-private
verify-private:
	$(PYTHON) $(SCRIPTS)/verify_private_space.py \
		--requester verify-requester \
		--participant verify-participant \
		--outsider verify-outsider

.PHONY: enroll-players
enroll-players:
	@for i in 1 2 3 4 5 6 7; do \
		echo "=== Enrolling mafia-player-$$i ==="; \
		$(PYTHON) $(SCRIPTS)/enroll_agent.py --agent-name "mafia-player-$$i" || exit 1; \
		echo; \
	done

.PHONY: clean-state
clean-state:
	@echo "This will REMOVE all .intent-space-* directories. Press Ctrl+C in 5s to cancel."
	@sleep 5
	rm -rf .intent-space-*
	@echo "Cleaned."

.PHONY: logs
logs:
	@echo "--- Latest enrollment log ---"
	@ls -t artifacts/enroll-*.log 2>/dev/null | head -1 | xargs -I{} tail -20 {} || echo "(no enroll logs yet)"
	@echo
	@echo "--- Latest smoke log ---"
	@ls -t artifacts/smoke-*.log 2>/dev/null | head -1 | xargs -I{} tail -20 {} || echo "(no smoke logs yet)"
	@echo
	@echo "--- Latest verify log ---"
	@ls -t artifacts/verify-*.log 2>/dev/null | head -1 | xargs -I{} tail -20 {} || echo "(no verify logs yet)"
