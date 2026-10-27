"""Model boundary (spec §8): envelopes, prompts, the Ollama client and the evaluation harness.

Nothing here is imported eagerly: ``osa.llm.external`` in particular is only loaded when the
operator turned the external provider on (ADR-0002, ADR-0012).
"""
