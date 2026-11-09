FROM mcr.microsoft.com/dotnet/sdk:10.0-noble AS build
RUN apt-get update && apt-get install -y --no-install-recommends cmake g++ make nlohmann-json3-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build --parallel 2 && ctest --test-dir build --output-on-failure
RUN dotnet publish host/Flamoris.Cantabile.Host.csproj -c Release -r linux-x64 --self-contained true -o /publish

FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libicu74 libstdc++6 libssl3t64 zlib1g libfluidsynth3 python3 && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home cantabile
WORKDIR /app
ARG SOURCE_REVISION
LABEL org.opencontainers.image.title="flamoris-cantabile" org.opencontainers.image.version="0.1.0" org.opencontainers.image.revision=$SOURCE_REVISION
COPY --from=build /publish/ /app/
COPY --from=build /src/build/libcantabile_core.so /app/libcantabile_core.so
COPY --from=build /src/build/cantabile-cli /app/cantabile-cli
COPY --from=build /src/build/audio/cantabile-render /app/cantabile-render
COPY --from=build /usr/share/doc/nlohmann-json3-dev/copyright /usr/share/doc/cantabile/nlohmann-json-copyright
COPY THIRD_PARTY.md LICENSE /usr/share/doc/cantabile/
COPY packaging/entrypoint.py /app/entrypoint.py
ENV DOTNET_EnableDiagnostics=0 LD_LIBRARY_PATH=/app CANTABILE_CORE_LIBRARY=/app/libcantabile_core.so CANTABILE_RENDERER=/app/cantabile-render CANTABILE_SOUNDFONT=/soundfonts/default-GM.sf2
USER 10001:10001
ENTRYPOINT ["python3", "/app/entrypoint.py"]
