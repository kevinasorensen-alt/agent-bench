# Contestant image for Aider. Pin AIDER_VERSION in the task spec before a published run.
FROM python:3.12-slim
ARG AIDER_VERSION=0.86.2
RUN pip install --no-cache-dir aider-chat==${AIDER_VERSION} \
 && useradd -m bench
USER bench
ENV HOME=/home/bench
WORKDIR /work
