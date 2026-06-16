# opencode Agent Instructions

## Testing
- The full test suite takes ~5 minutes and WILL time out with the default 120s timeout.
- Always ask the user before running the full test suite — it's often not worth it for small changes.
- Running targeted subsets of tests (e.g., `pytest tests/fsms/test_els_fsm.py`) is fine to verify specific changes.
- Tests hang on headless Linux due to Kivy display init — may need `xvfb-run` or similar.

## Running the UI
- You're running in WSL on a Windows PC, NOT the target Raspberry Pi system.
- To launch the UI, you need: `DISPLAY=:0 SDL_AUDIODRIVER=dummy KIVY_INPUT=mouse uv run python -m rcp.main --size=1024x600`
  (See `runme.sh` for the full command — adapt it as needed for your context.)

## Memory
- You are connected to OpenBrain as an MCP. Query it (`search_thoughts`) at the start of a session — it may have memories related to the current work.
