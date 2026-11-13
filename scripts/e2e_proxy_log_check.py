"""Check one E2E run's proxy and api logs for leaked SSE tickets, leaked
refresh cookies and CSP violations (plan item 27, decision 0085; plan item
34, option 2).

Input: `docker compose logs --no-color --timestamps` for web and api (the
whole stack's log is fine; lines are told apart by compose's service prefix).

An SSE stream's single-use ticket travels in the query string, because
EventSource cannot send headers. Two logs see those requests:
- nginx (web) logs the path without its query string (apps/web/nginx.conf),
  so no web line may contain `ticket=` at all;
- the api's access log redacts the value (logging_filters.py), so an api line
  may hold `ticket=[redacted]` and nothing else after `ticket=`.
nginx's error log cannot be reformatted and quotes the whole request line on
an upstream error; it shares the web service's output, so it is covered by
the same rule.

The refresh token travels only in the `tenderos_refresh` cookie (plan item 34,
option 2). Neither nginx's log format nor uvicorn's access log has headers, so
no line of any service may contain `tenderos_refresh=`.

The CSP is enforced and reports violations to /csp-report (nginx.conf): any
`/csp-report` request in the web log is a page the browser blocked something
on.

Both checks would pass vacuously on a log with no stream traffic, so the run
fails unless both logs show at least one stream request.

Exit 0 when everything holds; 1 with one line per problem.
Run: python scripts/e2e_proxy_log_check.py e2e-logs/compose.log
"""

import argparse
import re
import sys
from pathlib import Path

# `web-1  | ...` or `api-1  | ...` (compose's prefix, --no-color).
_SERVICE = re.compile(r"^(?P<service>[\w.-]+?)-\d+\s+\|\s?(?P<rest>.*)$")
_STREAM = re.compile(r"/(workflow-runs|operations)/[^/ ?]+/events/stream")
_TICKET = re.compile(r"ticket=")
_UNREDACTED_TICKET = re.compile(r"ticket=(?!\[redacted\])")
_CSP_REPORT = re.compile(r"/csp-report\b")
_REFRESH_COOKIE = re.compile(r"tenderos_refresh=")


def check(lines: list[str]) -> tuple[list[str], dict[str, int]]:
    counts = {"web stream lines": 0, "api stream lines": 0, "csp reports": 0}
    problems: list[str] = []
    for number, line in enumerate(lines, 1):
        match = _SERVICE.match(line)
        if not match:
            continue
        service, rest = match["service"], match["rest"]
        if _REFRESH_COOKIE.search(rest):
            problems.append(f"line {number}: {service} log holds the refresh cookie")
        if service == "web":
            if _STREAM.search(rest):
                counts["web stream lines"] += 1
            if _TICKET.search(rest):
                problems.append(f"line {number}: web (nginx) log holds a ticket parameter")
            if _CSP_REPORT.search(rest):
                counts["csp reports"] += 1
                problems.append(f"line {number}: a CSP violation was reported")
        elif service == "api":
            if _STREAM.search(rest):
                counts["api stream lines"] += 1
            if _UNREDACTED_TICKET.search(rest):
                problems.append(f"line {number}: api log holds an unredacted ticket")
    for name in ("web stream lines", "api stream lines"):
        if counts[name] == 0:
            problems.append(f"no {name}: the check would pass without stream traffic")
    return problems, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("log", type=Path, help="docker compose logs output")
    args = parser.parse_args()
    lines = args.log.read_text(encoding="utf-8", errors="replace").splitlines()
    problems, counts = check(lines)
    summary = ", ".join(f"{name}: {n}" for name, n in counts.items())
    for problem in problems:
        print(problem)
    if problems:
        print(f"FAILED ({summary})")
        return 1
    print(
        "proxy logs: no SSE ticket in nginx's or the api's log, no refresh cookie, "
        f"no CSP violation ({summary})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
