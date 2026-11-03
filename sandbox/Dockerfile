# Sandbox image: Anvil (Foundry v1.8.5, pinned) + precompiled demo contracts.
# Contracts are compiled in the build stage; the final image has no solc and no sources.

# ---- build stage: compile with solc 0.8.30 ------------------------------------
FROM ghcr.io/foundry-rs/foundry:v1.8.5 AS build
USER root
WORKDIR /build
COPY foundry.toml ./
COPY src ./src
RUN forge build \
 && mkdir -p artifacts \
 && for c in MockSweeper MockToken; do \
      forge inspect "$c" deployedBytecode > "artifacts/$c.hex" \
      && grep -Eq '^0x[0-9a-f]+$' "artifacts/$c.hex" || exit 1; \
    done

# ---- runtime stage --------------------------------------------------------------
FROM ghcr.io/foundry-rs/foundry:v1.8.5
LABEL org.opencontainers.image.title="fail-closed-sandbox" \
      fail-closed.sandbox="1" \
      fail-closed.foundry="v1.8.5" \
      fail-closed.solc="0.8.30"
COPY --from=build --chown=root:root /build/artifacts /sandbox/artifacts
# Build fails if solc leaked into the runtime image or the artifact is missing.
RUN ! command -v solc \
 && [ ! -d /home/foundry/.svm ] && [ ! -d /root/.svm ] \
 && grep -Eq '^0x[0-9a-f]+$' /sandbox/artifacts/MockSweeper.hex \
 && grep -Eq '^0x[0-9a-f]+$' /sandbox/artifacts/MockToken.hex
USER foundry
WORKDIR /tmp
# The runner passes the anvil arguments (hardfork, chain id, host/port).
ENTRYPOINT ["anvil"]
