# Contestant image for plain Claude Code. Pin CLAUDE_CODE_VERSION in the task spec before a published run.
FROM node:22-slim
ARG CLAUDE_CODE_VERSION=latest
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
 && apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
USER node
WORKDIR /work
