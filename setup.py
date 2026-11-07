"""setup.py: copies the pinned loom-skills (vendor/loom-skills/skills) into the package as
weaver/_vendor/skills, so `pip install .` and future wheels carry the exact skills weaver was tested with."""
import os
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py

HERE = os.path.dirname(os.path.abspath(__file__))


class BuildWithSkills(build_py):
    def run(self):
        super().run()
        src = os.path.join(HERE, "vendor", "loom-skills", "skills")
        if not os.path.isdir(src):
            raise SystemExit("vendor/loom-skills is empty: git submodule update --init")
        dst = os.path.join(self.build_lib, "weaver", "_vendor", "skills")
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # which commit, for `weaver skills` (weaver/skills.py reads "sha date")
        import subprocess
        r = subprocess.run(["git", "-C", os.path.dirname(src), "log", "-1", "--format=%H %cs"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            with open(os.path.join(dst, "COMMIT"), "w") as f:
                f.write(r.stdout.strip() + "\n")


setup(cmdclass={"build_py": BuildWithSkills})
