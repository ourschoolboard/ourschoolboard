import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline"
sys.path.insert(0, str(PIPELINE))
SPEC = importlib.util.spec_from_file_location("pipeline_video_transcript", PIPELINE / "video_transcript.py")
VIDEO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VIDEO)


class VideoTranscriptTests(unittest.TestCase):
    def test_extract_audio_uses_fixed_ffmpeg_command_and_finds_chunks(self):
        def run(command, **_kwargs):
            output = Path(command[-1].replace("%05d", "00000"))
            output.write_bytes(b"mp3")
            return SimpleNamespace(returncode=0, stderr=b"")

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            VIDEO.subprocess, "run", side_effect=run
        ) as invoked:
            root = Path(temporary)
            source = root / "arbitrary-video-input"
            source.write_bytes(b"video")
            chunks = VIDEO.extract_audio_chunks(source, root, chunk_seconds=300)
        self.assertEqual(["audio-00000.mp3"], [item.name for item in chunks])
        command = invoked.call_args.args[0]
        self.assertEqual("ffmpeg", command[0])
        self.assertIn("-map", command)
        self.assertIn("0:a:0", command)
        self.assertIn("file,pipe", command)
        self.assertNotIn("shell=True", command)

    def test_openrouter_request_uses_requested_qwen_model_and_raw_base64(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"text": "A useful transcript.", "usage": {"seconds": 3.2}},
        )
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"OPENROUTER_API_KEY": "secret"}
        ), patch.object(VIDEO, "safe_post", return_value=response) as post:
            audio = Path(temporary) / "audio.mp3"
            audio.write_bytes(b"audio")
            text, usage = VIDEO.transcribe_audio(
                audio, model="qwen/qwen3-asr-1.7b", language="en"
            )
        self.assertEqual("A useful transcript.", text)
        self.assertEqual(3.2, usage["seconds"])
        payload = __import__("json").loads(post.call_args.kwargs["data"])
        self.assertEqual("qwen/qwen3-asr-1.7b", payload["model"])
        self.assertEqual("YXVkaW8=", payload["input_audio"]["data"])
        self.assertNotIn("data:", payload["input_audio"]["data"])

    def test_empty_model_output_is_rejected(self):
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"text": " "})
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"OPENROUTER_API_KEY": "secret"}
        ), patch.object(VIDEO, "safe_post", return_value=response):
            audio = Path(temporary) / "audio.mp3"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(VIDEO.VideoTranscriptError, "empty"):
                VIDEO.transcribe_audio(
                    audio, model="qwen/qwen3-asr-1.7b", language=None
                )

    def test_render_labels_machine_transcript_and_chunk_offsets(self):
        rendered = VIDEO.render_transcript(
            "https://official.example/meeting.mov", ["first", "second"],
            model="qwen/qwen3-asr-1.7b", language=None, chunk_seconds=300,
            usage=[{}, {}],
        )
        self.assertIn("machine-generated speech recognition", rendered)
        self.assertIn("[00:00:00]", rendered)
        self.assertIn("[00:05:00]", rendered)

    def test_chunk_transcription_defaults_to_three_workers_and_keeps_order(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            VIDEO, "extract_audio_chunks"
        ) as extract, patch.object(
            VIDEO, "transcribe_audio"
        ) as transcribe, patch.object(
            VIDEO, "ThreadPoolExecutor", wraps=VIDEO.ThreadPoolExecutor
        ) as executor:
            root = Path(temporary)
            chunks = [root / f"audio-{index}.mp3" for index in range(4)]
            for chunk in chunks:
                chunk.write_bytes(b"audio")
            extract.return_value = chunks
            transcribe.side_effect = [
                (f"chunk {index}", {}) for index in range(4)
            ]
            destination = root / "transcript.txt"
            VIDEO.fetch_to_file(
                "https://official.example/meeting.mp4", root / "video",
                destination, {}, max_bytes=10000,
            )
            rendered = destination.read_text()
        executor.assert_called_once_with(max_workers=3)
        self.assertLess(rendered.index("chunk 0"), rendered.index("chunk 3"))

    def test_empty_primary_uses_automatic_fallback_and_labels_chunk(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            VIDEO, "extract_audio_chunks"
        ) as extract, patch.object(
            VIDEO, "transcribe_audio"
        ) as transcribe:
            root = Path(temporary)
            chunk = root / "audio-00000.mp3"
            chunk.write_bytes(b"audio")
            extract.return_value = [chunk]
            transcribe.side_effect = [
                VIDEO.VideoTranscriptError("OpenRouter returned an empty transcription"),
                ("fallback transcript", {"seconds": 300, "cost": 0.001}),
            ]
            destination = root / "transcript.txt"
            VIDEO.fetch_to_file(
                "https://official.example/meeting.mp4", root / "video",
                destination,
                {
                    "model": "qwen/qwen3-asr-1.7b",
                },
                max_bytes=10000,
            )
            rendered = destination.read_text()
        self.assertEqual(2, transcribe.call_count)
        self.assertEqual(
            "qwen/qwen3-asr-0.6b", transcribe.call_args_list[1].kwargs["model"]
        )
        self.assertIn(
            "Automatic fallback transcription models: qwen/qwen3-asr-0.6b, "
            "nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b",
            rendered,
        )
        self.assertIn("[00:00:00] [qwen/qwen3-asr-0.6b]", rendered)

    def test_primary_already_in_fallback_chain_is_not_retried(self):
        self.assertEqual(
            [
                "qwen/qwen3-asr-0.6b",
                "nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b",
            ],
            VIDEO.transcription_models("qwen/qwen3-asr-0.6b"),
        )


if __name__ == "__main__":
    unittest.main()
