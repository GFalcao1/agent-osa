"""Search over the index: full-text in Portuguese with registry filters (TASK-109).

The query envelope comes from the model (TASK-110) or from the command line; the search
itself is code over the database and never reads a document from disk.
"""
