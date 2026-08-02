# Office documents (.rtf/.doc/.docx/.odt) -> Markdown.
# LibreOffice lives here rather than on the host; the converter itself needs
# only bs4, so this image stays free of the PDF/OCR dependency stack.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui \
        fonts-liberation \
        fonts-dejavu-core \
        python3 \
        python3-bs4 \
    && rm -rf /var/lib/apt/lists/*

# LibreOffice needs a writable HOME for its per-run user profile.
ENV HOME=/tmp \
    PYTHONPATH=/opt/scripts \
    PYTHONUNBUFFERED=1

WORKDIR /corpus
ENTRYPOINT ["python3", "/opt/scripts/convert_office.py"]
CMD ["--help"]
