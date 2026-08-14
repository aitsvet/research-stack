# PlantUML diagrams (.puml) -> PNG/SVG.
# Java and Graphviz live here rather than on the host, for the same reason
# LibreOffice does in docconv: a paper's figures are regenerated rarely, and
# a JDK on the host earns nothing between runs.
#
# The VERSION is pinned and must stay pinned. Debian's packaged plantuml is
# 1.2020.02, and rendering existing sources with it changes every figure —
# an untouched .puml came back 18 % smaller with different spacing. Committed
# figures then drift whenever anything is regenerated, which reads as an
# edit nobody made. 1.2024.7 is the version the existing corpus was rendered
# with; bump it only deliberately, and re-render every diagram in one pass.
#
# Graphviz is not optional — PlantUML renders sequence diagrams on its own but
# hands component/class layouts to `dot`, and without it those diagrams come
# back as an error image rather than failing loudly.
FROM debian:bookworm-slim

ARG PLANTUML_VERSION=1.2024.7

RUN apt-get update && apt-get install -y --no-install-recommends \
        default-jre-headless \
        graphviz \
        fonts-dejavu-core \
        fonts-liberation \
        ca-certificates \
        curl \
    && curl -fsSL -o /opt/plantuml.jar \
        "https://github.com/plantuml/plantuml/releases/download/v${PLANTUML_VERSION}/plantuml-${PLANTUML_VERSION}.jar" \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# DejaVu Sans is what the .puml files ask for by name; without a UTF-8 locale
# PlantUML mangles Cyrillic labels into boxes. `-charset UTF-8` on the command
# line matters for the same reason — it is baked into the entrypoint so a
# caller cannot forget it.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/tmp

WORKDIR /work
ENTRYPOINT ["java", "-jar", "/opt/plantuml.jar", "-charset", "UTF-8"]
CMD ["-version"]
