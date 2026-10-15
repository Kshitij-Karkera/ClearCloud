import logging
import os
import sys
import time
import unittest
from collections import OrderedDict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))

USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(text, code):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def green(t): return paint(t, "32")
def red(t): return paint(t, "31")
def yellow(t): return paint(t, "33")
def bold(t): return paint(t, "1")
def dim(t): return paint(t, "2")


def describe(test):
    doc = (test._testMethodDoc or "").strip().splitlines()
    if doc:
        return doc[0]
    return test._testMethodName.replace("test_", "").replace("_", " ")


class DetailedResult(unittest.TestResult):
    def __init__(self, out):
        super().__init__()
        self.out = out
        self.rows = []
        self.by_class = OrderedDict()
        self._started = 0.0
        self._current_class = None

    def _say(self, text=""):
        self.out.write(text + "\n")

    def startTest(self, test):
        super().startTest(test)
        cls = f"{type(test).__module__}.{type(test).__name__}"
        if cls != self._current_class:
            self._current_class = cls
            self._say()
            self._say(bold(f"== {cls}"))
        self._say(dim(
            f"   [{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] RUN   {test._testMethodName}"))
        self._say(dim(f"                          what: {describe(test)}"))
        self._started = time.perf_counter()

    def _finish(self, test, status, colour, detail=""):
        ms = (time.perf_counter() - self._started) * 1000
        cls = f"{type(test).__module__}.{type(test).__name__}"
        self.rows.append((cls, test._testMethodName, status, ms))
        self.by_class.setdefault(cls, []).append(status)
        self._say(
            f"   [{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {colour(status.ljust(5))} {test._testMethodName}  ({ms:.2f} ms)")
        if detail:
            for line in detail.rstrip().splitlines():
                self._say("      | " + line)

    def addSuccess(self, test):
        super().addSuccess(test)
        self._finish(test, "PASS", green)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._finish(test, "FAIL", red, self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._finish(test, "ERROR", red, self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._finish(test, "SKIP", yellow, f"reason: {reason}")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._say(red(f"      subtest failed: {subtest}"))
            for line in self._exc_info_to_string(err, test).rstrip().splitlines():
                self._say("      | " + line)


def main():
    out = sys.stdout

    logging.basicConfig(
        level=logging.DEBUG,
        format="      log %(levelname)s %(name)s: %(message)s",
        stream=out,
    )

    out.write(bold("ClearCloud test run\n"))
    out.write(f"  started : {datetime.now().isoformat(timespec='seconds')}\n")
    out.write(f"  python  : {sys.version.split()[0]} on {sys.platform}\n")
    out.write(f"  folder  : {HERE}\n")

    suite = unittest.defaultTestLoader.discover(HERE, pattern="test_*.py")
    out.write(f"  tests   : {suite.countTestCases()}\n")

    result = DetailedResult(out)
    started = time.perf_counter()
    suite.run(result)
    total = time.perf_counter() - started

    out.write("\n" + bold("Summary by class") + "\n")
    for cls, statuses in result.by_class.items():
        passed = statuses.count("PASS")
        line = f"  {cls:<55} {passed}/{len(statuses)} passed"
        out.write((green(line) if passed == len(
            statuses) else red(line)) + "\n")

    slowest = sorted(result.rows, key=lambda r: r[3], reverse=True)[:3]
    out.write("\n" + bold("Slowest tests") + "\n")
    for cls, name, _, ms in slowest:
        out.write(f"  {ms:8.2f} ms  {name}\n")

    ok = result.wasSuccessful()
    out.write("\n")
    out.write(
        f"  ran {result.testsRun} tests in {total:.3f}s | "
        f"{len(result.rows) - len(result.failures) - len(result.errors) - len(result.skipped)} passed, "
        f"{len(result.failures)} failed, {len(result.errors)} errors, {len(result.skipped)} skipped\n"
    )
    out.write("  " + (green("RESULT: OK")
              if ok else red("RESULT: FAILED")) + "\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
