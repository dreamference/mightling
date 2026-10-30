"""The vLLM log streamer keeps every line of the engine's output and always closes the pipe.

A pipe that fails mid-read or on close does not take the reader down.
"""

from dreamference.vllm_server.vllm_log_streamer import VLLMLogStreamer


class _Pipe:
    def __init__(self, lines, fail_after=None, close_fails=False):
        self.lines = list(lines)
        self.fail_after = fail_after
        self.close_fails = close_fails
        self.reads = 0
        self.closed = False

    def readline(self):
        if self.fail_after is not None and self.reads >= self.fail_after:
            raise OSError("pipe broke")
        self.reads += 1
        return self.lines.pop(0) if self.lines else ""

    def close(self):
        self.closed = True
        if self.close_fails:
            raise OSError("already closed")


def test_lines_are_queued_without_their_line_endings_and_the_pipe_is_closed():
    streamer, pipe = VLLMLogStreamer(), _Pipe(["INFO one\n", "INFO two\r\n", ""])
    streamer._enqueue_output(pipe)
    assert streamer.pop_logs() == ["INFO one", "INFO two"]
    assert streamer.pop_logs() == []
    assert pipe.closed is True


def test_a_pipe_that_breaks_mid_read_keeps_the_lines_read_and_is_still_closed():
    streamer, pipe = VLLMLogStreamer(), _Pipe(["INFO one\n", "INFO two\n"], fail_after=1)
    streamer._enqueue_output(pipe)
    assert streamer.pop_logs() == ["INFO one"]
    assert pipe.closed is True


def test_a_pipe_that_fails_to_close_does_not_raise():
    streamer, pipe = VLLMLogStreamer(), _Pipe(["INFO one\n"], close_fails=True)
    streamer._enqueue_output(pipe)
    assert streamer.pop_logs() == ["INFO one"] and pipe.closed is True


def test_a_read_that_returns_nothing_but_not_an_empty_string_ends_the_stream():
    streamer, pipe = VLLMLogStreamer(), _Pipe(["INFO one\n", None, "INFO after\n"])
    streamer._enqueue_output(pipe)
    assert streamer.pop_logs() == ["INFO one"]
    assert pipe.lines == ["INFO after\n"] and pipe.closed is True


def test_streaming_reads_the_pipe_on_its_own_thread_until_the_end():
    streamer, pipe = VLLMLogStreamer(), _Pipe(["INFO one\n", "INFO two\n"])
    streamer.start_streaming(pipe)
    streamer.log_thread.join(timeout=5)
    assert not streamer.log_thread.is_alive() and streamer.log_thread.daemon is True
    assert streamer.pop_logs() == ["INFO one", "INFO two"] and pipe.closed is True
