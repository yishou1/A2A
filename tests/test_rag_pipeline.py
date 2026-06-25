import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_agents.rag.ingest import (
    PdfExtractionResult,
    PdfPage,
    ingest_documents,
)
from decision_agents.rag.documents import load_rag_chunks
from decision_agents.rag.pipeline import run_rag


class RagPipelineTest(unittest.TestCase):
    def test_markdown_knowledge_base_loads_rule_chunks(self):
        chunks = load_rag_chunks()

        self.assertTrue(chunks)
        self.assertTrue(any(chunk.rule_id == "AUTH-STATE-PENDING" for chunk in chunks))
        first = chunks[0]
        self.assertTrue(first.rule_id)
        self.assertIsInstance(first.tags, tuple)
        self.assertTrue(first.text)

    def test_disabled_local_models_fall_back_to_keyword_retrieval(self):
        env = {
            **os.environ,
            "ENABLE_LOCAL_RAG_MODELS": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            result = run_rag(
                "pending authorization decision-support review",
                purpose="compliance",
                top_k=3,
            )

        self.assertTrue(result.evidence)
        self.assertTrue(result.answer)
        self.assertFalse(result.model_profile["enabled"])
        self.assertTrue(
            any("rag_onnx_models_disabled" in warning for warning in result.warnings)
        )

    def test_enabled_onnx_models_without_files_fall_back_cleanly(self):
        env = {
            **os.environ,
            "ENABLE_RAG_ONNX_MODELS": "true",
            "RAG_EMBEDDING_ONNX_MODEL": "missing/embedding.onnx",
            "RAG_RERANK_ONNX_MODEL": "missing/rerank.onnx",
            "RAG_QUERY_ONNX_MODEL": "missing/query.onnx",
            "RAG_GENERATION_ONNX_MODEL": "missing/generation.onnx",
        }
        with patch.dict(os.environ, env, clear=True):
            result = run_rag(
                "pending authorization decision-support review",
                purpose="compliance",
                top_k=3,
            )

        self.assertTrue(result.evidence)
        self.assertTrue(result.answer)
        self.assertTrue(result.model_profile["enabled"])
        self.assertEqual(result.model_profile["backend"], "onnxruntime")
        self.assertTrue(any("onnx_model_not_found" in warning for warning in result.warnings))

    def test_pdf_roe_ingest_writes_sqlite_chunks_and_skips_unchanged_docs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "roe_docs"
            source_dir.mkdir()
            pdf_path = source_dir / "sample_roe.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 mock roe content")
            index_path = Path(temp_dir) / "rag_index.sqlite"
            env = {
                **os.environ,
                "ENABLE_LOCAL_RAG_MODELS": "false",
                "RAG_INDEX_PATH": str(index_path),
            }
            with patch.dict(os.environ, env, clear=True), patch(
                "decision_agents.rag.ingest.extract_pdf_pages",
                return_value=_sample_pdf_extraction(),
            ):
                first = ingest_documents(source=source_dir)
                second = ingest_documents(source=source_dir)
                rebuilt = ingest_documents(source=source_dir, rebuild=True)

        self.assertEqual(first.documents_seen, 1)
        self.assertEqual(first.documents_ingested, 1)
        self.assertGreater(first.chunks_written, 0)
        self.assertEqual(second.documents_skipped, 1)
        self.assertEqual(rebuilt.documents_ingested, 1)

    def test_rag_retrieval_returns_pdf_evidence_with_citation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "roe_docs"
            source_dir.mkdir()
            pdf_path = source_dir / "authorization_roe.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 mock authorization content")
            index_path = Path(temp_dir) / "rag_index.sqlite"
            env = {
                **os.environ,
                "ENABLE_LOCAL_RAG_MODELS": "false",
                "RAG_INDEX_PATH": str(index_path),
            }
            with patch.dict(os.environ, env, clear=True), patch(
                "decision_agents.rag.ingest.extract_pdf_pages",
                return_value=_sample_pdf_extraction(),
            ):
                ingest_documents(source=source_dir)
                result = run_rag(
                    "authorization required for simulation only decision support",
                    purpose="compliance",
                    top_k=3,
                    document_scope="roe",
                )

        self.assertTrue(result.evidence)
        pdf_evidence = result.evidence[0]
        self.assertEqual(pdf_evidence.doc_type, "roe")
        self.assertEqual(pdf_evidence.source, "authorization_roe.pdf")
        self.assertEqual(pdf_evidence.page_start, 1)
        self.assertIn("p.1", pdf_evidence.citation)
        self.assertTrue(result.answer)

    def test_scan_like_pdf_without_ocr_records_warning_without_failing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "roe_docs"
            source_dir.mkdir()
            pdf_path = source_dir / "scan_roe.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 mock scanned content")
            env = {
                **os.environ,
                "ENABLE_LOCAL_RAG_MODELS": "false",
                "ENABLE_RAG_OCR": "false",
                "RAG_INDEX_PATH": str(Path(temp_dir) / "rag_index.sqlite"),
            }
            extraction = PdfExtractionResult(
                pages=[PdfPage(page=1, text="")],
                warnings=["ocr_disabled:scan_roe.pdf:page:1"],
            )
            with patch.dict(os.environ, env, clear=True), patch(
                "decision_agents.rag.ingest.extract_pdf_pages",
                return_value=extraction,
            ):
                summary = ingest_documents(source=source_dir)

        self.assertEqual(summary.documents_ingested, 0)
        self.assertEqual(summary.documents_skipped, 1)
        self.assertEqual(summary.chunks_written, 0)
        self.assertTrue(any("ocr_disabled" in warning for warning in summary.warnings))
        self.assertTrue(any("pdf_no_chunks" in warning for warning in summary.warnings))


def _sample_pdf_extraction() -> PdfExtractionResult:
    return PdfExtractionResult(
        pages=[
            PdfPage(
                page=1,
                text=(
                    "ROE-001: Authorization Required\n"
                    "Simulation-only decision support must remain inside approved "
                    "authorization scope and requires review before handoff."
                ),
            ),
            PdfPage(
                page=2,
                text=(
                    "ROE-002: Restricted Effects\n"
                    "Plans must avoid restricted effects and retain audit evidence."
                ),
            ),
        ],
    )


if __name__ == "__main__":
    unittest.main()
