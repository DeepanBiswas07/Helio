import sys
import os

from PyQt5.QtCore import QObject, QThread, pyqtSignal

# sys.path is configured by app.py at startup — src/ is already on the path.
# Import run_agent here at module load time (not inside the thread) so that
# chat_memory's conversation_history deque is always the SAME shared instance
# across every agent call, preserving conversation context across messages.
from agent import run_agent


class AgentThread(QThread):
    """Runs run_agent(query) in a background thread so the UI never blocks."""
    response_ready = pyqtSignal(str)  # Emitted with the agent's final response text
    tool_executed = pyqtSignal()      # Emitted when a visual application/browser/file is opened

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    def run(self):
        try:
            import tools.tool_executor as te
            te.ON_TOOL_EXECUTE_CALLBACK = self.tool_executed.emit
        except ImportError:
            pass

        try:
            result = run_agent(self.query)
            self.response_ready.emit(result or "")
        except Exception as e:
            print(f"[Agent] Error processing query: {e}")
            self.response_ready.emit("")
        finally:
            try:
                import tools.tool_executor as te
                te.ON_TOOL_EXECUTE_CALLBACK = None
            except ImportError:
                pass


class AgentBridge(QObject):
    """
    Thin coordinator between the STT pipeline and the Helio agent.
    Connect voice_manager.transcribed -> AgentBridge.on_transcription.
    Connect AgentBridge.response_ready -> your response handler.
    """
    response_ready = pyqtSignal(str)  # Forwarded from AgentThread when done

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None

    def on_transcription(self, text: str):
        """Called with the transcribed speech text. Fires off an agent query."""
        if not text.strip():
            return

        print(f"\n[Agent] Received query: \"{text}\"")
        print("[Agent] Processing...\n")

        self._thread = AgentThread(text)
        self._thread.response_ready.connect(self._on_agent_done)
        self._thread.tool_executed.connect(self._on_tool_executed)
        self._thread.start()

    def abort_active_query(self):
        """Abort the active running thread if any, by disconnecting its signal."""
        if self._thread and self._thread.isRunning():
            try:
                self._thread.response_ready.disconnect(self._on_agent_done)
                self._thread.tool_executed.disconnect(self._on_tool_executed)
                print("[Agent] Active query processing aborted.")
            except TypeError:
                pass  # Already disconnected
            self._thread = None

    def _on_agent_done(self, response: str):
        if response:
            print(f"\n[Helio] {response}\n")
        self.response_ready.emit(response)

    def _on_tool_executed(self):
        """Collapse the fullscreen window immediately when a browser/app/file is opened."""
        parent = self.parent()
        if parent and hasattr(parent, '_space_overlay'):
            if parent._space_overlay.isVisible():
                print("[AgentBridge] Closing fullscreen visual space because an external application is being opened.")
                parent._space_overlay.close_space()

