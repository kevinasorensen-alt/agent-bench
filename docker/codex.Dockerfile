# Contestant image for plain Codex CLI. Pin CODEX_VERSION in the task spec before a published run.
FROM node:22-slim
ARG CODEX_VERSION=latest
RUN npm install -g @openai/codex@${CODEX_VERSION} \
 && apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates git \
 && rm -rf /var/lib/apt/lists/*
USER node
WORKDIR /work
