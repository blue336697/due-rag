"""文档摄取领域服务。"""
from __future__ import annotations

import hashlib, os, tempfile, uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import yaml

from service.core.config import get_config, get_managed_knowledge_dir
from service.ingestion.parsers import assess_quality, parse_file, render_markdown
from service.ingestion.registry import IngestionRegistry
from service.schemas.ingestion import CanonicalDocument


class IngestionError(RuntimeError): pass


class IngestionService:
    def __init__(self, registry: Optional[IngestionRegistry] = None):
        self.cfg = get_config(); self.options = self.cfg["ingestion"]
        self.registry = registry or IngestionRegistry(self.cfg["paths"]["ingestion_dir"])

    def accept_upload(self, filename: str, content: bytes, domain: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        safe_name = Path(filename or "upload").name
        extension = Path(safe_name).suffix.lower()
        if extension not in self.options["allowed_extensions"]: raise IngestionError(f"unsupported extension: {extension}")
        if not content: raise IngestionError("empty upload")
        if len(content) > self.options["max_upload_bytes"]: raise IngestionError("upload exceeds configured size limit")
        if domain not in self.cfg["knowledge"]["domains"] or not self.cfg["knowledge"]["domains"][domain].get("enabled", True):
            raise IngestionError(f"unknown or disabled domain: {domain}")
        source_hash = "sha256:" + hashlib.sha256(content).hexdigest()
        duplicate = self.registry.find_by_hash(domain, source_hash)
        if duplicate: return {**duplicate, "duplicate": True}
        document_id = f"doc_{uuid.uuid4().hex[:16]}"
        raw_path = self.registry.save_raw(document_id, extension, content)
        record = {"document_id": document_id, "domain": domain, "source_filename": safe_name, "source_type": extension,
                  "source_hash": source_hash, "raw_path": str(raw_path), "metadata": metadata, "status": "uploaded",
                  "created_at": datetime.now().isoformat(), "duplicate": False}
        self.registry.put_document(document_id, record)
        return record

    def process(self, document_id: str) -> CanonicalDocument:
        record = self.registry.get_document(document_id)
        if not record: raise IngestionError("document not found")
        ocr_cfg = self.options.get("ocr", {})
        elements, parser_meta = parse_file(Path(record["raw_path"]), record["source_filename"], ocr_config=ocr_cfg)
        metadata = {**record.get("metadata", {}), **parser_meta}
        quality = assess_quality(elements, record["source_type"], self.options["min_text_chars"], parser_meta, float(ocr_cfg.get("min_confidence", 60)))
        title = next((e.text for e in elements if e.type == "title"), Path(record["source_filename"]).stem)
        markdown = render_markdown(elements, title)
        canonical = CanonicalDocument(document_id=document_id, domain=record["domain"], source_filename=record["source_filename"],
            source_type=record["source_type"], source_hash=record["source_hash"], title=title, metadata=metadata,
            elements=elements, normalized_markdown=markdown, quality=quality)
        self.registry.save_canonical(document_id, canonical.model_dump(mode="json"), markdown)
        record.update({"status": "awaiting_review" if quality.requires_review else "ready", "quality": quality.model_dump(), "title": title})
        self.registry.put_document(document_id, record)
        return canonical

    def publish(self, document_id: str, force: bool = False) -> Path:
        record = self.registry.get_document(document_id); data = self.registry.load_canonical(document_id)
        if not record or not data: raise IngestionError("document is not parsed")
        canonical = CanonicalDocument(**data)
        if not canonical.quality.passed and not force: raise IngestionError("quality gate failed; force is required")
        managed = Path(get_managed_knowledge_dir(canonical.domain)).resolve(); managed.mkdir(parents=True, exist_ok=True)
        target = (managed / f"{document_id}.md").resolve()
        if target.parent != managed: raise IngestionError("managed publish path escapes configured root")
        frontmatter = {"title": canonical.title, "document_id": document_id, "source_filename": canonical.source_filename,
            "source_hash": canonical.source_hash, **canonical.metadata}
        content = "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=True) + "---\n\n" + canonical.normalized_markdown
        fd, temp = tempfile.mkstemp(dir=managed, prefix=f".{document_id}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f: f.write(content); f.flush(); os.fsync(f.fileno())
            os.replace(temp, target)
        finally:
            if os.path.exists(temp): os.unlink(temp)
        record.update({"status": "published", "published_path": str(target), "published_at": datetime.now().isoformat()})
        self.registry.put_document(document_id, record); return target

    def delete(self, document_id: str) -> Dict[str, Any]:
        record = self.registry.get_document(document_id)
        if not record: raise IngestionError("document not found")
        path = record.get("published_path")
        if path:
            target = Path(path).resolve(); managed = Path(get_managed_knowledge_dir(record["domain"])).resolve()
            if target.parent == managed and target.exists(): target.unlink()
        record.update({"status": "deleted", "deleted_at": datetime.now().isoformat()}); self.registry.put_document(document_id, record)
        return record
