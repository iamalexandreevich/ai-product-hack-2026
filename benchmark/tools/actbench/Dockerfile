# Build from benchmark/: docker build --build-context contracts=../contracts \
#   -f tools/actbench/Dockerfile -t agentgate-actbench .
FROM agentgate-bench-sandbox:latest
USER root
RUN git clone https://github.com/ZJUICSR/ActBench.git /opt/actbench \
 && git -C /opt/actbench checkout --detach e532aa45fdb77ac81565fa5151d2652ffd94c828 \
 && /home/bench/venv/bin/pip install --no-cache-dir /opt/actbench httpx jsonschema
COPY --from=contracts *.schema.json /contracts/
COPY --chown=bench:bench . /work
RUN git config --system --add safe.directory /opt/actbench
USER bench
WORKDIR /work
ENTRYPOINT ["python", "tools/actbench.py"]
