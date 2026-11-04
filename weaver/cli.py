"""weaver: port, optimize and serve Hugging Face LLMs on AMD Loom, run by a local coding agent."""
import argparse
import subprocess
import sys

from weaver import __version__

EPILOG = """\
CHECKPOINT: huggingface://org/repo[:file.gguf], org/repo, or a huggingface.co URL.

examples:
  weaver init
  weaver serve huggingface://unsloth/Qwen3.5-0.8B-GGUF
  weaver optimize huggingface://unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-GGUF beat-llamacpp 2h
  weaver agent -m huggingface://loom-fun/unsloth-flint-loom
"""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="weaver", description=__doc__, epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"weaver {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="show the agent's full messages")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    sub.add_parser("init", help="fetch and start the local agent model (Qwen3.8-27B), set everything up") \
        .add_argument("--skip-llamacpp", action="store_true", help="don't pre-fetch the llama.cpp reference")
    sub.add_parser("doctor", help="check this machine")

    def ck(sp):
        sp.add_argument("checkpoint", metavar="CHECKPOINT")
        sp.add_argument("--no-spec", action="store_true", help="no speculative decoding (the skills' SPEC=off)")
        return sp

    s = ck(sub.add_parser("serve", help="serve a checkpoint (porting it first if needed) and open the chat UI"))
    s.add_argument("--port", type=int, help="model port (default 8080)")
    s.add_argument("--ui-port", type=int, help="playground port (default 8090)")
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--keep-agent", action="store_true", help="keep the agent server running")
    s.add_argument("--preset", help="for a published or catalog card with several presets (default: chat)")

    s = ck(sub.add_parser("port", help="port a checkpoint to Loom, validate it, basic optimizations"))
    s.add_argument("--force", action="store_true", help="port again even if a passing port exists")

    s = ck(sub.add_parser("optimize", help="port if needed, then optimize toward TARGET within BUDGET"))
    s.add_argument("target", nargs="?", metavar="TARGET",
                   help="roofline (default), beat-llamacpp, or e.g. 'decode+30%%'")
    s.add_argument("budget", nargs="?", metavar="BUDGET", help="e.g. 90m or 3h (default: 30 min x agent slowdown)")

    s = sub.add_parser("upload", help="publish a ported checkpoint's Loom package and model card to a HF org")
    s.add_argument("checkpoint", metavar="CHECKPOINT")
    s.add_argument("org", metavar="HF-ORG")
    s.add_argument("-y", "--yes", action="store_true", help="publish without the confirmation prompt")
    s.add_argument("--private", action="store_true", help="create the Hugging Face repo as private")

    s = sub.add_parser("agent", help="interactive Pi session on the local agent model, or on CHECKPOINT with -m")
    s.add_argument("-m", "--model", metavar="CHECKPOINT", help="serve this checkpoint and use it as the model")
    s.add_argument("--ctx-size", type=int, help="cap the context window Pi is told about")
    s.add_argument("--agent-args", default="", help="extra Pi flags, e.g. \"--thinking high\"")
    s.add_argument("--no-loom-skills", action="store_true", help="a plain coding agent, without the loom skills")
    s.add_argument("--user-config", action="store_true",
                   help="write into ~/.pi/agent (keeps your other providers) instead of weaver's own Pi config")
    s.add_argument("--keep-agent", action="store_true", help="with -m: keep the agent server running")
    s.add_argument("--keep-server", action="store_true", help="leave the model server(s) running when Pi exits")
    s.add_argument("--port", type=int, help="with -m: model port (default 8080)")
    s.add_argument("--preset", help="with -m: the card preset (default: chat)")
    s.add_argument("--no-spec", action="store_true", help=argparse.SUPPRESS)

    s = sub.add_parser("agent-server", help="manage the local agent model server")
    s.add_argument("action", choices=["start", "stop", "status", "bench"])

    s = sub.add_parser("resume", help="continue an interrupted port/optimize agent session")
    s.add_argument("checkpoint", metavar="CHECKPOINT")
    return p


def _ca_certificates() -> None:
    """Use certifi's CA bundle when the system has none (minimal containers): urllib, the HF
    client and loom-fetch then verify HTTPS the same way everywhere."""
    import os
    import ssl
    if os.environ.get("SSL_CERT_FILE"):
        return
    paths = ssl.get_default_verify_paths()
    if (paths.cafile and os.path.isfile(paths.cafile)) or (paths.capath and os.path.isdir(paths.capath) and os.listdir(paths.capath)):
        return
    try:
        import certifi
    except ImportError:
        return
    os.environ["SSL_CERT_FILE"] = certifi.where()


def main(argv: list[str] | None = None) -> int:
    _ca_certificates()
    p = _parser()
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 0
    if a.cmd == "doctor":
        from weaver.commands import doctor
        return doctor.run()
    if a.cmd == "init":
        from weaver.commands import init
        return init.run(skip_llamacpp=a.skip_llamacpp)

    from weaver import checkpoint, config
    from weaver.runtime import Runtime
    cfg = config.load()
    rt = Runtime(cfg.reserved_gib)

    def parse_ck(text):
        try:
            return checkpoint.parse(text)
        except ValueError as e:
            p.error(str(e))

    try:
        if a.cmd == "serve":
            from weaver.commands import serve
            return serve.run(rt, cfg, parse_ck(a.checkpoint), port=a.port, ui_port=a.ui_port,
                             browser=not a.no_browser, keep_agent=a.keep_agent, no_spec=a.no_spec, verbose=a.verbose,
                             preset=a.preset)
        if a.cmd == "port":
            from weaver import jobs
            rec = jobs.port(rt, cfg, parse_ck(a.checkpoint), force=a.force, no_spec=a.no_spec, verbose=a.verbose)
            return 0 if rec.get("gates") == "PASS" else 1
        if a.cmd == "optimize":
            from weaver import jobs
            rec = jobs.optimize(rt, cfg, parse_ck(a.checkpoint), a.target, a.budget, no_spec=a.no_spec, verbose=a.verbose)
            return 0 if rec.get("audit") == "PASS" else 1
        if a.cmd == "upload":
            from weaver.commands import upload
            return upload.run(rt, cfg, parse_ck(a.checkpoint), a.org, yes=a.yes, private=a.private, verbose=a.verbose)
        if a.cmd == "agent":
            from weaver.commands import agent
            return agent.run(rt, cfg, parse_ck(a.model) if a.model else None, ctx_size=a.ctx_size,
                             agent_args=a.agent_args, loom_skills=not a.no_loom_skills, user_config=a.user_config,
                             keep_agent=a.keep_agent, keep_server=a.keep_server, port=a.port, no_spec=a.no_spec,
                             preset=a.preset)
        if a.cmd == "agent-server":
            from weaver.commands import agent_server_cmd
            return agent_server_cmd.run(rt, cfg, a.action)
        if a.cmd == "resume":
            from weaver import jobs, paths
            ck = parse_ck(a.checkpoint)
            res = jobs.run_agent(rt, cfg, paths.work_dir(ck.slug), "Continue where you stopped. Finish the task "
                                 "and end with the WEAVER_RESULT line.", cont=True, verbose=a.verbose)
            rec = jobs.record_port(ck, res, "resume")
            return 0 if rec.get("gates") == "PASS" else 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
        if a.verbose:
            raise
        print(f"weaver: error: {e}", file=sys.stderr)
        return 1
    return 2
