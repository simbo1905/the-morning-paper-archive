PORT ?= 8080
PIDFILE := .tmp/nginx.pid
LOGFILE := .tmp/nginx-error.log
CONF := .tmp/nginx.local.conf

.PHONY: serve stop restart status test oracle

serve: $(CONF)
	@mkdir -p .tmp
	@if [ -f $(PIDFILE) ] && kill -0 $$(cat $(PIDFILE)) 2>/dev/null; then \
		echo "already serving on http://127.0.0.1:$(PORT) (pid $$(cat $(PIDFILE)))"; \
	else \
		nginx -c $(CURDIR)/$(CONF) \
		&& echo "serving $(CURDIR) on http://127.0.0.1:$(PORT) (pid $$(cat $(PIDFILE)))"; \
	fi

stop:
	@if [ -f $(PIDFILE) ] && kill -0 $$(cat $(PIDFILE)) 2>/dev/null; then \
		kill -QUIT $$(cat $(PIDFILE)); rm -f $(PIDFILE); echo "stopped"; \
	else \
		rm -f $(PIDFILE); echo "not running"; \
	fi

restart: stop serve

status:
	@if [ -f $(PIDFILE) ] && kill -0 $$(cat $(PIDFILE)) 2>/dev/null; then \
		echo "serving on http://127.0.0.1:$(PORT) (pid $$(cat $(PIDFILE)))"; \
	else \
		echo "not running"; \
	fi

$(CONF): web/nginx.local.conf.in
	@mkdir -p .tmp
	sed -e 's|@ROOT@|$(CURDIR)|' \
	    -e 's|@PORT@|$(PORT)|' \
	    -e 's|@PID@|$(CURDIR)/$(PIDFILE)|' \
	    -e 's|@LOG@|$(CURDIR)/$(LOGFILE)|' $< > $@

test:
	./scripts/test_corpus_clean.py
	./scripts/test_whoosh_oracle.py
	cargo test --quiet --manifest-path wasm-test/tantivy-native/Cargo.toml
	cargo test --quiet --manifest-path wasm-test/tantivy-search/Cargo.toml

oracle:
	./scripts/whoosh_oracle.py build --md
	./scripts/whoosh_oracle.py battery --md
