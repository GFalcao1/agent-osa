"""The courier (SPEC §2, §5, §6): deterministic code that decides and performs effects.

The model only writes envelopes (``osa.llm``); everything that touches the index or the
filesystem lives here, behind injected ports, so the flow is testable without a bot, a
real model or a share. Entry points: ``pipeline.process`` (one file from the quarantine or
a watched folder to its destination or to review), ``undo.undo_operation`` and
``owners`` (declared owner, upload root, new-owner requests).
"""
