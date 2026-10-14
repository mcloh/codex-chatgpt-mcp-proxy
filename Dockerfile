# Base digest is also recorded in deploy/base-images.json.
ARG PYTHON_IMAGE=python@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock \
    && groupadd --gid 10001 bridge && useradd --uid 10001 --gid bridge --no-create-home bridge \
    && mkdir -p /data && chown bridge:bridge /data
COPY --chown=10001:10001 src ./src
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "codex_chatgpt_mcp_proxy.cli", "server", "--host", "0.0.0.0"]
