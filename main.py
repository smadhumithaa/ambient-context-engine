"""
AI Call Copilot - Real-time AI assistant overlay for video calls
Invisible to screen sharing, visible only to you.
"""
import threading
import sys
import config               # loads .env before anything else
from overlay.window import CopilotOverlay
from audio.listener import AudioListener
from ai.engine import AIEngine


def main():
    # Validate config and print summary
    try:
        config.validate()
    except ValueError as exc:
        print(f"\n[Config] Configuration error:\n{exc}\n")
        print("[Config] Edit your .env file (copy .env.example if you haven't yet) and restart.")
        sys.exit(1)

    # Initialize components
    ai_engine = AIEngine()
    overlay   = CopilotOverlay(
        ai_provider=config.AI_PROVIDER,
        ai_model=config.AI_MODEL,
        stt_provider=config.STT_PROVIDER,
        stt_model=config.STT_MODEL,
    )
    listener  = AudioListener(
        on_question_detected=lambda transcript: _on_question(transcript, ai_engine, overlay)
    )

    # Wire the X / Alt+Q close button to stop the listener cleanly
    overlay.set_on_close(listener.stop)

    # Start audio listener in background
    listener_thread = threading.Thread(target=listener.start, daemon=True)
    listener_thread.start()

    overlay.set_status("[Listening]")
    print("[Copilot] Started. Listening for questions on your call...")
    print("[Copilot] Overlay controls:  X / Alt+Q = quit  |  pause btn / Alt+P = pause  |  Alt+H = hide")
    print("[Copilot] To manually test: type a question in the terminal and press Enter.")
    print()

    # Allow typing test questions directly in the terminal
    _start_terminal_test(ai_engine, overlay)

    # Run overlay (blocking - main thread)
    try:
        overlay.run()
    except KeyboardInterrupt:
        listener.stop()
        print("\n[Copilot] Stopped.")
        sys.exit(0)


def _on_question(transcript: str, ai_engine: AIEngine, overlay: CopilotOverlay):
    """Called when a question is detected in audio OR typed in terminal."""
    # Skip if paused
    if overlay.is_paused:
        print("[Copilot] Paused — ignoring question.")
        return
    threading.Thread(
        target=handle_question,
        args=(transcript, ai_engine, overlay),
        daemon=True,
    ).start()


def handle_question(transcript: str, ai_engine: AIEngine, overlay: CopilotOverlay):
    """Send question to AI and display answer in overlay."""
    overlay.set_status("[Thinking...]")
    overlay.add_message("You", transcript, role="user")

    answer = ai_engine.answer(transcript)
    overlay.add_message("Copilot", answer, role="assistant")
    overlay.set_status("[Listening]")


def _start_terminal_test(ai_engine: AIEngine, overlay: CopilotOverlay):
    """
    Lets you type questions directly into the terminal to test the overlay
    without needing to speak into the microphone.
    """
    def _read_input():
        print("[Test] Type a question and press Enter to test the overlay:")
        while True:
            try:
                line = input("> ").strip()
                if line:
                    print(f"[Test] Sending: {line}")
                    _on_question(line, ai_engine, overlay)
            except (EOFError, KeyboardInterrupt):
                break

    t = threading.Thread(target=_read_input, daemon=True)
    t.start()


if __name__ == "__main__":
    main()
