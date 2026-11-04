# Recovery reference for the Asterisk 22.11.0 pilot. Prefer a saved live image.
FROM debian:bookworm-slim
ARG ASTERISK_VERSION=22.11.0
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config ca-certificates wget bzip2 patch python3 \
    libedit-dev libjansson-dev libxml2-dev libsqlite3-dev uuid-dev \
    libssl-dev libncurses-dev libcurl4-openssl-dev libsrtp2-dev \
    libspeexdsp-dev libunbound-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src
RUN wget -q https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}.tar.gz \
    && wget -q https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}.sha256 \
    && sha256sum -c asterisk-${ASTERISK_VERSION}.sha256 \
    && tar -xzf asterisk-${ASTERISK_VERSION}.tar.gz

WORKDIR /usr/src/asterisk-${ASTERISK_VERSION}
RUN ./configure --prefix=/usr --libdir=/usr/lib \
       --sysconfdir=/etc --localstatedir=/var --with-pjproject-bundled \
    && make menuselect.makeopts \
    && menuselect/menuselect --disable BUILD_NATIVE \
       --enable chan_websocket --enable res_websocket_client \
       --enable res_http_websocket --enable app_echo menuselect.makeopts \
    && make -j2 \
    && make install \
    && make samples \
    && test -f /usr/lib/asterisk/modules/chan_websocket.so \
    && test -f /usr/lib/asterisk/modules/codec_alaw.so \
    && test -f /usr/lib/asterisk/modules/codec_resample.so \
    && groupadd --system --gid 10001 asterisk \
    && useradd --system --uid 10001 --gid asterisk --home-dir /var/lib/asterisk asterisk \
    && mkdir -p /var/run/asterisk /var/log/asterisk /var/spool/asterisk \
    && chown -R asterisk:asterisk /var/lib/asterisk /var/run/asterisk \
       /var/log/asterisk /var/spool/asterisk

USER asterisk
CMD ["asterisk", "-f", "-vvv"]
