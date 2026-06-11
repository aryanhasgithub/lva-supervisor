ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.14-alpine3.22-2026.05.0
FROM ${BUILD_FROM} AS supervisor-base

# S6 and System environment
ENV \
    S6_SERVICES_GRACETIME=415000 \
    S6_KILL_GRACETIME=3000 \
    UV_SYSTEM_PYTHON=true \
    PYTHONDONTWRITEBYTECODE=1

# Install runtime dependencies
RUN apk add --no-cache libffi eudev

# Install uv for the build and runtime
RUN pip3 install uv==0.11.18

##############################################
# Build stage
##############################################
FROM supervisor-base AS supervisor-build

WORKDIR /usr/src

# Install requirements - all deps have pre-built musllinux wheels on PyPI,
# no compiler toolchain needed
RUN --mount=type=bind,source=./requirements.txt,target=/usr/src/requirements.txt \
    uv pip install --compile-bytecode --no-cache --no-build -r requirements.txt

# Copy and install supervisor
ARG BUILD_VERSION="0.0.1.dev0"
COPY . .

RUN sed -i "s/^SUPERVISOR_VERSION =.*/SUPERVISOR_VERSION = \"${BUILD_VERSION}\"/g" \
        /usr/src/lva_supervisor/const.py \
    && uv pip install --no-cache --compile-bytecode . \
    && python3 -m compileall ./lva_supervisor/

# Copy rootfs files
COPY rootfs /
RUN chmod +x /etc/services.d/*/run /etc/services.d/*/finish 2>/dev/null || true

#########################
# Final flattened image #
#########################
FROM supervisor-base

# Copy everything from the build stage as a single layer
COPY --from=supervisor-build / /

LABEL \
    io.lva.type="supervisor" \
    org.opencontainers.image.title="LVA Supervisor" \
    org.opencontainers.image.description="Supervisor for managing LVA-OS containers" \
    org.opencontainers.image.authors="aryanhasgithub" \
    org.opencontainers.image.url="https://github.com/aryanhasgithub/lva-os" \
    org.opencontainers.image.licenses="Apache License 2.0"