"""Repo-root pytest configuration.

Force Kivy's mock GL/window backends for the entire test suite. Any test that
imports the app or a dispatcher pulls in Kivy; on WSLg (and headless CI) a real
SDL2/OpenGL context takes ~135 s to initialize and needs a display, which is
what made test collection crawl. No pytest test renders anything (the README
screenshot capture is a standalone script, not a test), so mock graphics is
safe and makes imports near-instant.

This MUST run before any `import kivy` selects a backend. A repo-root conftest
is imported by pytest before collecting any test module, so this is the earliest
reliable hook.
"""
import os

os.environ.setdefault("KIVY_GL_BACKEND", "mock")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_NO_ARGS", "1")
