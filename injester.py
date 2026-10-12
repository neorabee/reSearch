import os
import re
import json
import hashlib
from magic import Magic

from bs4 import BeautifulSoup
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer


# --------------------------------------------------
# Configuration
# --------------------------------------------------

folder_path = "sources/"
output_dir = "extracted_texts"

# Chunking settings (characters, roughly 4 chars per token)
CHUNK_MAX_CHARS = 3000   # ~500 words per chunk
CHUNK_OVERLAP = 300      # overlap so boundary sentences stay whole in one chunk

os.makedirs(output_dir, exist_ok=True)

files = [
    os.path.join(folder_path, filename)
    for filename in os.listdir(folder_path)
    if os.path.isfile(os.path.join(folder_path, filename))
]

mime_type_detector = Magic(mime=True)


# --------------------------------------------------
# Text cleaning
# --------------------------------------------------

def clean_text(text):
    """
    Basic cleanup while preserving the actual content.
    """

    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Fix words split across lines:
    # "govern-\nment" -> "government"
    text = re.sub(
        r'([A-Za-z]+)-\n([A-Za-z]+)',
        r'\1\2',
        text
    )

    # Strip whitespace from both ends of each line
    lines = [line.strip() for line in text.split("\n")]

    # Collapse multiple blank lines
    cleaned_lines = []
    previous_empty = False

    for line in lines:
        if not line:
            if not previous_empty:
                cleaned_lines.append("")
            previous_empty = True
        else:
            cleaned_lines.append(line)
            previous_empty = False

    # Normalize multiple spaces
    text = "\n".join(cleaned_lines)
    text = re.sub(r'[ \t]+', ' ', text)

    return text.strip()


# --------------------------------------------------
# Chunking
# --------------------------------------------------

def chunk_text(text, max_chars=CHUNK_MAX_CHARS, overlap=CHUNK_OVERLAP):
    """
    Split text into overlapping chunks of up to max_chars,
    preferring to cut at paragraph boundaries.
    """

    chunks = []
    start = 0

    while start < len(text):

        end = min(start + max_chars, len(text))

        if end < len(text):
            # Try to break at a paragraph boundary in the back half
            cut = text.rfind("\n\n", start, end)

            if cut <= start + max_chars // 2:
                # Fall back to a sentence boundary
                cut = text.rfind(". ", start, end)
                if cut <= start + max_chars // 2:
                    cut = end
                else:
                    cut += 1  # keep the period
        else:
            cut = end

        chunk = text[start:cut].strip()

        if chunk:
            chunks.append(chunk)

        if cut >= len(text):
            break

        # Step forward, keeping some overlap, but always make progress
        start = max(cut - overlap, start + 1)

    return chunks


def make_chunks(text, document_id, page_number=None):
    """
    Build chunk records with traceable IDs.
    PDF:  <docid8>_p<page>_c<n>
    HTML: <docid8>_c<n>
    """

    prefix = document_id[:8]

    if page_number is not None:
        prefix = f"{prefix}_p{page_number}"

    return [
        {
            "chunk_id": f"{prefix}_c{i}",
            "text": chunk
        }
        for i, chunk in enumerate(chunk_text(text), start=1)
    ]


# --------------------------------------------------
# PDF extraction
# --------------------------------------------------

def extract_pdf(file_path, document_id):
    """
    Extract text page-by-page from a PDF, with chunks per page.
    """

    pages = []

    for page_number, page_layout in enumerate(
        extract_pages(file_path),
        start=1
    ):

        page_text_parts = []

        for element in page_layout:
            if isinstance(element, LTTextContainer):
                page_text_parts.append(element.get_text())

        page_text = "".join(page_text_parts)

        page_text = clean_text(page_text)

        pages.append({
            "page_number": page_number,
            "text": page_text,
            "chunks": make_chunks(page_text, document_id, page_number)
        })

    return pages


# --------------------------------------------------
# HTML extraction
# --------------------------------------------------

def extract_html(file_path, document_id):
    """
    Extract visible text from an HTML/HTM file, with chunks.
    """

    with open(
        file_path,
        "r",
        encoding="utf-8",
        errors="replace"
    ) as f:

        html = f.read()

    soup = BeautifulSoup(html, "lxml")

    # Remove elements that don't contain useful document text
    for element in soup([
        "script",
        "style",
        "noscript"
    ]):
        element.decompose()

    # Extract visible text
    text = soup.get_text(separator="\n")

    text = clean_text(text)

    return {
        "text": text,
        "chunks": make_chunks(text, document_id)
    }


# --------------------------------------------------
# Document processing
# --------------------------------------------------

document_data = []

for file in files:

    try:

        # ------------------------------------------
        # Identify file
        # ------------------------------------------

        mime_type = mime_type_detector.from_file(file)

        with open(file, "rb") as f:
            document_id = hashlib.md5(
                f.read()
            ).hexdigest()

        file_size = os.path.getsize(file)

        filename = os.path.basename(file)

        print(
            f"\nFile: {filename}"
            f"\nMIME: {mime_type}"
        )


        # ------------------------------------------
        # PDF
        # ------------------------------------------

        if mime_type == "application/pdf":

            print("→ Extracting PDF page-by-page")

            pages = extract_pdf(file, document_id)

            total_chunks = sum(len(p["chunks"]) for p in pages)
            print(f"→ {len(pages)} pages, {total_chunks} chunks")

            document = {
                "document_id": document_id,
                "filename": filename,
                "file_size": file_size,
                "mime_type": mime_type,
                "source_type": "pdf",
                "pages": pages
            }


        # ------------------------------------------
        # HTML / HTM
        # ------------------------------------------

        elif mime_type in [
            "text/html",
            "application/xhtml+xml"
        ]:

            print("→ Extracting HTML")

            html_data = extract_html(file, document_id)

            print(f"→ {len(html_data['chunks'])} chunks")

            document = {
                "document_id": document_id,
                "filename": filename,
                "file_size": file_size,
                "mime_type": mime_type,
                "source_type": "html",
                "text": html_data["text"],
                "chunks": html_data["chunks"]
            }


        # ------------------------------------------
        # Unsupported
        # ------------------------------------------

        else:

            print(
                f"⚠️ Unsupported file type: {mime_type}"
            )

            continue


        # ------------------------------------------
        # Save
        # ------------------------------------------

        document_data.append(document)

        base_name = os.path.splitext(filename)[0]

        # Include part of the document hash so report.pdf and
        # report.html (or duplicate names) don't overwrite each other
        json_path = os.path.join(
            output_dir,
            f"{base_name}_{document_id[:8]}.json"
        )

        with open(
            json_path,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                document,
                f,
                ensure_ascii=False,
                indent=4
            )

        print(
            f"✅ Saved: {json_path}"
        )


    except Exception as e:

        print(
            f"❌ Failed to process {file}: {e}"
        )


# --------------------------------------------------
# Save overall document index
# --------------------------------------------------

with open(
    "document_index.json",
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        document_data,
        f,
        ensure_ascii=False,
        indent=4
    )


print("\nDone!")