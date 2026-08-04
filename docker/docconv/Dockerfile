# Office documents (.rtf/.doc/.docx/.odt) -> Markdown.
# LibreOffice lives here rather than on the host; the converter itself needs
# only bs4, so this image stays free of the PDF/OCR dependency stack.
# p7zip/unzip ride along because document bundles arrive as nested .zip/.rar
# (cbr.ru message albums) and unpacking them on the host would mean installing
# archive tooling there — override the entrypoint to use them:
#   docker compose --profile tools run --rm --entrypoint sh docconv -c '7z x ...'
FROM debian:bookworm-slim

# RAR5 needs the non-free unrar: unar's engine drops files out of the cbr.ru
# albums ("Attempted to read more data than was available") and Debian's 7-Zip
# build has the RAR codec removed, so both fail silently-ish on this corpus.
# The base image ships deb822 sources with Signed-By, so a second .list for the
# same suite errors out — widen Components on the existing entry instead.
RUN sed -i 's/^Components: .*/Components: main contrib non-free/' \
        /etc/apt/sources.list.d/debian.sources

RUN apt-get update && apt-get install -y --no-install-recommends \
        unrar \
        libreoffice-writer-nogui \
        fonts-liberation \
        fonts-dejavu-core \
        python3 \
        python3-bs4 \
        7zip \
        p7zip-full \
        unar \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# LibreOffice needs a writable HOME for its per-run user profile.
# LANG matters for unrar: without a UTF-8 locale it writes Cyrillic entry names
# as "?????" and then fails to reopen its own paths.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/tmp \
    PYTHONPATH=/opt/scripts \
    PYTHONUNBUFFERED=1

WORKDIR /corpus
ENTRYPOINT ["python3", "/opt/scripts/convert_office.py"]
CMD ["--help"]
