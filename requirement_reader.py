from docx import Document
import io

def read_docx(content: bytes) -> str:
    """Extract plain text from a .docx file."""
    doc = Document(io.BytesIO(content))
    lines = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def read_txt(content: bytes) -> str:
    return content.decode("utf-8", errors="ignore")


def extract_requirements(filename: str, content: bytes) -> str:
    ext = filename.lower().split(".")[-1]
    if ext == "docx":
        return read_docx(content)
    elif ext in ("txt", "md"):
        return read_txt(content)
    else:
        raise ValueError(f"Unsupported requirements file: {ext}. Use .docx or .txt")
