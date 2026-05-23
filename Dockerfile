ARG BUILD_FROM=python:3.12-alpine3.19
FROM ${BUILD_FROM} AS supervisor-base

ENV \
    UV_SYSTEM_PYTHON=true \
    PYTHONDONTWRITEBYTECODE=1

# Only libffi needed at runtime for dbus-fast/aiohttp
RUN apk add --no-cache libffi

##############################################
# Build stage  C extensions need build deps #
##############################################
FROM supervisor-base AS supervisor-build

RUN apk add --no-cache \
        gcc \
        musl-dev \
        libc-dev \
        libffi-dev \
        python3-dev \
    && pip install uv==0.10.9

WORKDIR /usr/src

RUN --mount=type=bind,source=./requirements.txt,target=/usr/src/requirements.txt \
    uv pip install \
        --compile-bytecode \
        --no-cache \
        -r requirements.txt

ARG BUILD_VERSION="0.0.1.dev0"
COPY lva-supervisor/ ./lva-supervisor/

RUN sed -i "s/^SUPERVISOR_VERSION =.*/SUPERVISOR_VERSION = \"${BUILD_VERSION}\"/g" \
        /usr/src/lva-supervisor/const.py \
    && python3 -m compileall ./lva-supervisor/

#########################
# Final flattened image #
#########################
FROM supervisor-base

COPY --from=supervisor-build /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=supervisor-build /usr/local/bin /usr/local/bin
COPY --from=supervisor-build /usr/src/lva-supervisor /usr/src/lva-supervisor

WORKDIR /usr/src

LABEL \
    io.lva.type="supervisor" \
    org.opencontainers.image.title="LVA Supervisor" \
    org.opencontainers.image.description="Supervisor for managing LVA-OS containers" \
    org.opencontainers.image.authors="aryanhasgithub" \
    org.opencontainers.image.url="https://github.com/aryanhasgithub/lva-os" \
    org.opencontainers.image.licenses="Apache License 2.0"

CMD ["python", "-m", "lva-supervisor.main"]