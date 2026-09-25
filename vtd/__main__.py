import sys
from pathlib import Path
from vtd.cli import build_parser, run_pipeline


def _main_parser():
    return build_parser()

def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "meeting-structured":
        import json
        from vtd.cli import _build_meeting_v2_input
        from vtd.meeting_v2 import render_meeting_markdown_v2, render_meeting_html_v2
        source = Path(args.input_json)
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        raw = json.loads(source.read_text(encoding="utf-8"))
        data = _build_meeting_v2_input(raw)
        (output / "meeting_v2.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "INSTRUKCJA.md").write_text(render_meeting_markdown_v2(args.title, data, output), encoding="utf-8")
        render_meeting_html_v2(args.title, data, output / "INSTRUKCJA.html", output)
    elif args.command == "run":
        run_pipeline(
            video_path=Path(args.video_path),
            output_dir=Path(args.output),
            title=args.title,
            device=args.device,
            transcription_engine=getattr(args, "transcription_engine", "local-whisper"),
            model_size=args.model,
            diarization=getattr(args, "diarization", False),
            scene_threshold=args.scene_threshold,
            track=args.track,
            mode=args.mode,
            enrich=args.enrich,
            enrich_model=args.enrich_model,
            curate=getattr(args, "curate", False),
            curate_model=getattr(args, "curate_model", "deepseek/deepseek-flash"),
            curate_scope=getattr(args, "curate_scope", "program"),
            client=getattr(args, "client", None),
            process=getattr(args, "process", None),
            module=getattr(args, "module", None),
            environment=getattr(args, "environment", None),
            author=getattr(args, "author", None),
            document_status=getattr(args, "document_status", "DRAFT"),
            output_mode=getattr(args, "output_mode", "both"),
            max_step_seconds=getattr(args, "max_step_seconds", 45.0),
            grid_interval=getattr(args, "grid_interval", 30.0),
            frame_qa=getattr(args, "frame_qa", False),
            annotate=getattr(args, "annotate", False),
            nano_mode=getattr(args, "nano_mode", "located"),
            nano_banana=getattr(args, "nano_banana", False),
            nano_auto_accept=getattr(args, "nano_auto_accept", False),
            min_step_seconds=getattr(args, "min_step_seconds", 6.0),
            no_cache=getattr(args, "no_cache", False),
            cache_dir=getattr(args, "cache_dir", None),
            meeting_max_frames=getattr(args, "meeting_max_frames", 40),
            semantic_video_map=getattr(args, "semantic_video_map", False),
            semantic_model=getattr(args, "semantic_model", "gemini-2.5-flash"),
        )
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
