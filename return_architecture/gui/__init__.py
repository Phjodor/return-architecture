"""Streamlit-based GUI for managing a Return Architecture installation.

Entry point is `return_architecture.gui.app:main`. Run via the CLI:

    return-architecture gui

The GUI never holds state across sessions; configs on disk are the source
of truth. Edits made in the GUI write to the same files the daemon reads.
"""
