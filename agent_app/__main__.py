from .runtime import app_root


def main():
    import sys
    if '--review-worker' in sys.argv:
        from .review_process import worker_main
        raise SystemExit(worker_main(sys.argv[sys.argv.index('--review-worker')+1]))
    if '--version' in sys.argv:
        from . import __version__
        print(__version__)
        return 0
    root = app_root()
    root.mkdir(parents=True, exist_ok=True)
    if getattr(sys, 'frozen', False):
        import faulthandler
        logs = root/'.data/logs'
        logs.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = (logs/'desktop.log').open('a', encoding='utf-8', buffering=1)
        faulthandler.enable(file=sys.stderr)
    try:
        from .review_ui import run
        result = run(root)
    except Exception:
        import traceback
        traceback.print_exc()
        raise
    raise SystemExit(result)


if __name__ == "__main__":
    main()
